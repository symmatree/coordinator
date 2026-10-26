// The capture control loop, with no camera library in it.
//
// A backend (depthai for the OAK, libcamera for a campod) owns the device and its threads.
// It hands this every frame it retrieves and calls tick() regularly; this decides warmup,
// reads the arm gate, keeps the ledger, names and writes what is kept, and publishes the
// status document. It never calls back into the backend, so it suits a backend that polls
// (depthai's output queues) as well as one driven by callbacks (libcamera's
// requestCompleted), which only has to move each frame onto its own thread first.
//
// Not thread-safe: call it from one thread.
#pragma once

#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "durable_writer.hpp"

namespace capture {

struct SessionConfig {
    std::string node;
    std::string session;  // the boot id
    std::filesystem::path dir;  // captures/<node>/<session>
    // Every stream that has to deliver warmup_frames before the session is warm.
    std::vector<std::string> streams;
    int warmup_frames = 10;
    std::optional<std::filesystem::path> flag_path;
    std::optional<std::filesystem::path> status_path;
    std::string log_name;  // prefix for log lines, e.g. "oak-capture"
    // Camera sync is part of `ready`, and nothing is kept until it is reached: a campod's
    // frames are only usable with its partner's. The backend reports it with
    // set_sync_ready(). Without this, ready is warm.
    bool sync = false;
    // The ledger's file name in `dir`. Empty means <prefix>_<session>.frames.jsonl; the
    // campods' is frames.jsonl.
    std::string ledger_file{};
};

// One frame, as a backend saw it.
struct Frame {
    std::string kind;
    // The device's own frame number; a gap is a frame the host never received.
    int64_t device_seq = 0;
    // The frame's instant on CLOCK_MONOTONIC, as the backend defines it.
    int64_t monotonic_ns = 0;
    unsigned width = 0;
    unsigned height = 0;
    // Backend-specific values (exposure, gain, device clock, ...), copied into the ledger
    // line and, when kept, the sidecar.
    nlohmann::json fields = nlohmann::json::object();
};

class Session {
   public:
    explicit Session(SessionConfig cfg);
    ~Session();
    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;

    // Status fields outside the loop's own: the camera's presence, the device id, a
    // backend's counters. Published at the next publish().
    void set_status(const std::string& key, nlohmann::json value);
    void publish();

    // The device is open: start the ledger and the warmup. `file_prefix` begins every
    // file name (the OAK's MxId), so files stay keyed to the hardware that made them.
    void begin(const std::string& file_prefix);

    // Warmup and the arm gate, at most every 100 ms; the status, at most every second.
    void tick(double now_mono_s);

    // Every frame retrieved goes through here. If it is kept, `encode` is called for its
    // bytes and the file and sidecar are queued to the writer; otherwise it is not called,
    // so a paused session never pays for an encode. Its ledger line says which, as
    // `decision`: warmup, paused, sync_wait, kept, dropped (the writer's queue was full) or
    // encode_failed.
    //
    // Files are <prefix>_<kind>_<seq>_<utc><ext>, with its .json sidecar beside it. A
    // session of one stream leaves the kind out, <prefix>_<seq>_<utc>, as the campods'
    // names always have.
    void frame(const Frame& f, const char* ext, const std::function<std::vector<uint8_t>()>& encode);

    // Camera sync as the backend last saw it. Published, and part of `ready` and of
    // capturing(), when the session was configured with `sync`.
    void set_sync_ready(bool ready);

    // Warm, armed and (if configured) synced: whether anything is being written, for
    // streams the backend writes itself (the OAK's IMU).
    bool capturing() const { return warm_ && armed_ && (!cfg_.sync || sync_ready_); }

    // Drain the writer, close the ledger, publish the stopped state. Idempotent.
    void stop();

    // Stop, publishing `phase: failed` and the reason instead of `stopped`, so a reader can
    // tell a capture that ended from one that broke.
    void fail(const std::string& why);

    struct Counts {
        int64_t seen = 0;
        int64_t kept = 0;
        int64_t device_seq_gaps = 0;
        std::optional<int64_t> last_device_seq;
    };
    const Counts& counts(const std::string& kind) const { return streams_.at(kind); }
    uint64_t writer_dropped() const { return writer_.dropped(); }

   private:
    SessionConfig cfg_;
    nlohmann::json state_;
    std::map<std::string, Counts> streams_;
    Writer writer_;
    std::FILE* ledger_ = nullptr;
    std::string prefix_;
    bool warm_ = false;
    bool armed_ = false;
    bool sync_ready_ = false;
    bool stopped_ = false;
    bool failed_ = false;
    double last_gate_ = -1e9;
    double last_publish_ = -1e9;
};

}  // namespace capture
