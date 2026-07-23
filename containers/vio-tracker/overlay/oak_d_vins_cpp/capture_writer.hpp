// coordinator #89: power-loss-safe background writer for the #72 capture artifacts.
//
// The tracker runs off the avionics 5 V rail, so an in-flight power cut is a yank
// mid-write. The stills/disparity path used to `cv::imwrite` straight to the final
// path on the tracker's hot loop with no fsync -- so on a cut the OS page cache lost
// the whole unflushed window (the 0-byte-tail we saw on 260712) and a torn file
// could survive as a valid-looking name.
//
// This owns the durable write OFF the hot path:
//   * encoding stays on the caller (same CPU as before);
//   * the caller hands over already-encoded bytes;
//   * a background thread writes <path>.tmp -> fsync -> rename to <path>, then
//     fsyncs the directory -- so each file appears atomically complete-or-absent;
//   * the queue is BOUNDED and drops images under backpressure instead of blocking,
//     so a slow SD never stalls feature tracking. The IMU/feature `.feat` stream does
//     NOT go through here -- it is never dropped by image backpressure.
//
// Deliberately self-contained (POSIX + std only, no depthai/OpenCV) so it unit-tests
// standalone: see test_capture_writer.cpp.
#pragma once

#include <atomic>
#include <cerrno>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <queue>
#include <string>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <unistd.h>

namespace capture {

// One durable file write: tmp -> fsync -> atomic rename. Returns true on success;
// on any failure the temp file is removed and the final path is left untouched.
inline bool durable_write(const std::string& path, const void* data, size_t len) {
    std::string tmp = path + ".tmp";
    int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return false;
    const char* p = static_cast<const char*>(data);
    size_t off = 0;
    while (off < len) {
        ssize_t n = ::write(fd, p + off, len - off);
        if (n < 0) {
            if (errno == EINTR) continue;
            ::close(fd);
            ::unlink(tmp.c_str());
            return false;
        }
        off += static_cast<size_t>(n);
    }
    if (::fsync(fd) != 0) { ::close(fd); ::unlink(tmp.c_str()); return false; }
    if (::close(fd) != 0) { ::unlink(tmp.c_str()); return false; }
    if (::rename(tmp.c_str(), path.c_str()) != 0) { ::unlink(tmp.c_str()); return false; }
    return true;
}

// fsync a directory so a rename into it is durable across a power cut.
inline void fsync_dir(const std::string& dir) {
    int dfd = ::open(dir.c_str(), O_RDONLY);
    if (dfd >= 0) { ::fsync(dfd); ::close(dfd); }
}

struct Job {
    std::string path;                     // final image path (e.g. .../foo.jpg)
    std::vector<unsigned char> bytes;     // already-encoded image bytes
    std::string sidecar_path;             // final sidecar path (.json), "" to skip
    std::string sidecar;                  // sidecar JSON content
};

// Single-producer background writer with a bounded, drop-on-full queue.
class Writer {
public:
    explicit Writer(std::string dir, size_t max_queue = 4)
        : dir_(std::move(dir)), max_(max_queue), worker_([this] { run(); }) {}

    ~Writer() { stop(); }

    // Non-blocking. Enqueues the job for durable writing, or drops it (returns false,
    // bumping dropped()) if the queue is full -- so the caller's hot loop never stalls.
    bool submit(Job&& job) {
        {
            std::lock_guard<std::mutex> lk(m_);
            if (q_.size() >= max_) { ++dropped_; return false; }
            q_.push(std::move(job));
        }
        cv_.notify_one();
        return true;
    }

    // Drain the remaining queue, then join. Idempotent.
    void stop() {
        if (stopping_.exchange(true)) return;
        cv_.notify_all();
        if (worker_.joinable()) worker_.join();
    }

    uint64_t dropped() const { return dropped_.load(); }

private:
    void run() {
        for (;;) {
            Job job;
            {
                std::unique_lock<std::mutex> lk(m_);
                cv_.wait(lk, [this] { return !q_.empty() || stopping_.load(); });
                if (q_.empty()) return;  // stopping and drained
                job = std::move(q_.front());
                q_.pop();
            }
            if (durable_write(job.path, job.bytes.data(), job.bytes.size())) {
                if (!job.sidecar.empty())
                    durable_write(job.sidecar_path, job.sidecar.data(), job.sidecar.size());
                fsync_dir(dir_);
            }
        }
    }

    std::string dir_;
    size_t max_;
    std::queue<Job> q_;
    std::mutex m_;
    std::condition_variable cv_;
    std::atomic<bool> stopping_{false};
    std::atomic<uint64_t> dropped_{0};
    std::thread worker_;
};

}  // namespace capture
