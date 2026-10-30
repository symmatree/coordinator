// campod-capture: the campod still capture, on libcamera's C++ API.
//
// The port of capture.py. Same environment, same session directory, same file names and
// sidecar fields, same status document: a campod running this is read the same way by
// pod-link, the panel and the analysis. What is different is underneath -- the ISP hands
// over YUV420 and libjpeg-turbo encodes it straight from the camera's buffer, so there is
// no RGB conversion and no copy of the frame.
//
// This file is the libcamera half: the camera, its controls and reading each request. The
// control loop -- warmup, the arm gate, the ledger, what is written and the status -- is
// capture::Session (containers/capture-common), shared with oak-capture. See README.md.
#include <sys/mman.h>
#include <turbojpeg.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <thread>
#include <vector>

#include <libcamera/libcamera.h>
#include <nlohmann/json.hpp>

#include "campod.hpp"
#include "conventions.hpp"
#include "durable_writer.hpp"
#include "session.hpp"

namespace fs = std::filesystem;
namespace lc = libcamera;
using json = nlohmann::json;

namespace {

std::atomic<bool> g_stop{false};

void request_stop(int) { g_stop = true; }

int64_t env_int(const char* name, int64_t fallback) {
    const char* v = std::getenv(name);
    return (v && *v) ? std::atoll(v) : fallback;
}

std::string env_str(const char* name, const char* fallback) {
    const char* v = std::getenv(name);
    return (v && *v) ? v : fallback;
}

double mono_s() { return capture::clock_triple().mono_ns / 1e9; }

// A metadata value, or null where the camera did not report it -- which is worth seeing in
// the data rather than papering over.
template <typename T>
json opt(const std::optional<T>& v) {
    return v ? json(*v) : json(nullptr);
}

// Completed requests, moved from libcamera's thread to the loop's: Session is not
// thread-safe, and the loop is the only thread that touches it.
class Completed {
   public:
    void on_complete(lc::Request* r) {
        {
            std::lock_guard lk(m_);
            q_.push_back(r);
        }
        cv_.notify_one();
    }
    lc::Request* pop(std::chrono::milliseconds wait) {
        std::unique_lock lk(m_);
        if (!cv_.wait_for(lk, wait, [&] { return !q_.empty(); })) return nullptr;
        auto* r = q_.front();
        q_.pop_front();
        return r;
    }

   private:
    std::mutex m_;
    std::condition_variable cv_;
    std::deque<lc::Request*> q_;
};

// One YUV420 frame's three planes, in the camera's own memory.
struct Planes {
    std::array<const uint8_t*, 3> data{};
    std::array<int, 3> stride{};
};

// Each buffer mapped once, at start, rather than per frame.
class Mappings {
   public:
    ~Mappings() {
        for (auto& [fd, m] : maps_) ::munmap(m.first, m.second);
    }

    void map(const lc::FrameBuffer* buf, const lc::StreamConfiguration& sc) {
        const auto& planes = buf->planes();
        // Mapped length per dmabuf: the planes may share one, at offsets.
        std::map<int, size_t> lengths;
        for (const auto& p : planes) {
            auto& len = lengths[p.fd.get()];
            len = std::max(len, static_cast<size_t>(p.offset) + p.length);
        }
        for (const auto& [fd, len] : lengths) {
            if (maps_.count(fd)) continue;
            void* addr = ::mmap(nullptr, len, PROT_READ, MAP_SHARED, fd, 0);
            if (addr == MAP_FAILED) throw std::runtime_error("cannot map a camera buffer");
            maps_[fd] = {addr, len};
        }
        auto base = [&](const lc::FrameBuffer::Plane& p) {
            return static_cast<const uint8_t*>(maps_.at(p.fd.get()).first) + p.offset;
        };
        // libcamera's YUV420: chroma at half the luma stride and half the height. Three
        // planes if the pipeline describes them, else one buffer laid out in that order.
        const int y_stride = static_cast<int>(sc.stride);
        const size_t y_len = static_cast<size_t>(y_stride) * sc.size.height;
        const size_t c_len = static_cast<size_t>(y_stride / 2) * ((sc.size.height + 1) / 2);
        Planes out;
        out.stride = {y_stride, y_stride / 2, y_stride / 2};
        if (planes.size() == 3) {
            out.data = {base(planes[0]), base(planes[1]), base(planes[2])};
        } else {
            const uint8_t* b = base(planes[0]);
            out.data = {b, b + y_len, b + y_len + c_len};
        }
        planes_[buf] = out;
    }

    const Planes& at(const lc::FrameBuffer* buf) const { return planes_.at(buf); }

   private:
    std::map<int, std::pair<void*, size_t>> maps_;
    std::map<const lc::FrameBuffer*, Planes> planes_;
};

// libjpeg-turbo from YUV420 planes: no colour conversion, the ISP has done it.
class Jpeg {
   public:
    explicit Jpeg(int quality) : quality_(quality), tj_(tjInitCompress()) {
        if (!tj_) throw std::runtime_error("tjInitCompress failed");
    }
    ~Jpeg() { tjDestroy(tj_); }

    std::vector<uint8_t> encode(const Planes& p, unsigned width, unsigned height) {
        unsigned char* out = nullptr;
        unsigned long size = 0;
        const unsigned char* planes[3] = {p.data[0], p.data[1], p.data[2]};
        if (tjCompressFromYUVPlanes(tj_, planes, static_cast<int>(width), p.stride.data(), static_cast<int>(height),
                                    TJSAMP_420, &out, &size, quality_, 0) != 0) {
            const std::string why = tjGetErrorStr2(tj_);
            tjFree(out);
            throw std::runtime_error("jpeg encode: " + why);
        }
        std::vector<uint8_t> bytes(out, out + size);
        tjFree(out);
        return bytes;
    }

   private:
    int quality_;
    tjhandle tj_;
};

}  // namespace

int main() {
    std::signal(SIGTERM, request_stop);
    std::signal(SIGINT, request_stop);

    const std::string node = capture::node_name("CAMPOD_NODE_NAME", "/etc/host-hostname");
    const fs::path out_dir = env_str("CAMPOD_CAPTURE_DIR", "/captures");
    const unsigned width = static_cast<unsigned>(env_int("CAMPOD_CAPTURE_WIDTH", 0));
    const unsigned height = static_cast<unsigned>(env_int("CAMPOD_CAPTURE_HEIGHT", 0));
    const int quality = static_cast<int>(env_int("CAMPOD_JPEG_QUALITY", 90));
    const int32_t max_exposure_us = static_cast<int32_t>(env_int("CAMPOD_STILL_MAX_EXPOSURE_US", 1000));
    std::string focus = env_str("CAMPOD_STILL_FOCUS", "infinity");
    std::transform(focus.begin(), focus.end(), focus.begin(), ::tolower);
    const int64_t frame_duration_us = env_int("CAMPOD_FRAME_DURATION_US", 1'000'000);
    const unsigned buffer_count = static_cast<unsigned>(env_int("CAMPOD_BUFFER_COUNT", 2));
    // Required: there is no unsynced mode (campod.hpp says why), so a pod with no leader
    // named is a broken deployment, and says so at start rather than producing unusable
    // frames.
    const std::string sync_leader = env_str("CAMPOD_SYNC_LEADER", "");
    if (sync_leader.empty()) {
        std::cerr << "capture: CAMPOD_SYNC_LEADER is required -- name the pacesetter pod "
                     "(a single pod names itself and runs as the server)"
                  << std::endl;
        return 2;
    }
    const std::string sync_mode = campod::sync_mode_for(sync_leader, node);
    const float dioptres = campod::focus_dioptres(focus);

    // The session is the boot id, which the accel reader also uses, so both write into one
    // directory without anything handing the path between them (#211).
    const std::string session_id = capture::boot_id();
    const fs::path session_dir = out_dir / node / session_id;
    fs::create_directories(session_dir);
    capture::copy_manifests(session_dir, {{"/etc/container-image", ""}, {"/etc/fleet-image", "fleet-image"}});

    capture::Session session({
        .node = node,
        .session = session_id,
        .dir = session_dir,
        .streams = {"still"},
        .warmup_frames = static_cast<int>(env_int("CAMPOD_WARMUP_FRAMES", 10)),
        .flag_path = capture::env_path("CAMPOD_CAPTURE_FLAG", "/tmp/campod_capture"),
        .status_path = capture::env_path("CAMPOD_STATUS_FILE", "/tmp/campod_camera_status"),
        .log_name = "capture",
        .sync = true,
        .ledger_file = "frames.jsonl",
    });
    session.publish();

    // No camera at start is final for this boot. The kernel's sensor probe runs once, and
    // only -EPROBE_DEFER is retried; a sensor that did not answer it (imx708 "failed to read
    // chip id", -EIO) has no device until a reboot, and a CSI ribbon has no hotplug to
    // trigger another probe. So there is nothing to wait for: say so on the bus and exit.
    auto cm = std::make_unique<lc::CameraManager>();
    if (cm->start() != 0) {
        session.fail("libcamera's camera manager did not start");
        return 1;
    }
    if (cm->cameras().empty()) {
        session.set_status("camera", "absent");
        session.fail("libcamera reports NO cameras. Check the kernel log for the sensor probe "
                     "(journalctl -k | grep imx708) and that the ribbon is seated, both ends, "
                     "contacts toward the board. A reboot is needed to probe again.");
        return 1;
    }
    session.set_status("camera", "present");
    session.publish();
    {
        std::cout << "capture: libcamera " << lc::CameraManager::version() << " sees";
        for (const auto& c : cm->cameras())
            std::cout << " " << c->properties().get(lc::properties::Model).value_or("?") << " @ " << c->id();
        std::cout << std::endl;
    }

    // Anything thrown from here is published as `failed` with its message, rather than
    // leaving the last status document standing.
    std::shared_ptr<lc::Camera> camera;
    try {
        camera = cm->cameras().front();
        if (camera->acquire() != 0) throw std::runtime_error("cannot acquire " + camera->id());

        // StillCapture on the Pi pipeline is YUV420, sYCC and the sensor's full size: what
        // a JPEG wants, with no conversion on our side.
        auto config = camera->generateConfiguration({lc::StreamRole::StillCapture});
        auto& sc = config->at(0);
        sc.pixelFormat = lc::formats::YUV420;
        sc.colorSpace = lc::ColorSpace::Sycc;
        if (width && height) sc.size = lc::Size(width, height);
        // The IPA, and so the sync algorithm, runs once per queued request, not once per
        // sensor frame. With two, every frame has one waiting while we hold the other.
        sc.bufferCount = buffer_count;
        const auto validated = config->validate();
        if (validated == lc::CameraConfiguration::Invalid) throw std::runtime_error("camera configuration is invalid");
        if (validated == lc::CameraConfiguration::Adjusted)
            std::cout << "capture: libcamera adjusted the configuration to " << sc.toString() << std::endl;
        if (sc.pixelFormat != lc::formats::YUV420) throw std::runtime_error("no YUV420 output: " + sc.toString());
        if (camera->configure(config.get()) != 0) throw std::runtime_error("cannot configure " + sc.toString());
        lc::Stream* stream = sc.stream();

        lc::FrameBufferAllocator allocator(camera);
        if (allocator.allocate(stream) < 0) throw std::runtime_error("cannot allocate camera buffers");
        Mappings mappings;
        std::vector<std::unique_ptr<lc::Request>> requests;
        for (const auto& buf : allocator.buffers(stream)) {
            mappings.map(buf.get(), sc);
            auto r = camera->createRequest();
            if (!r || r->addBuffer(stream, buf.get()) != 0) throw std::runtime_error("cannot create a request");
            requests.push_back(std::move(r));
        }

        // The controls go in at start(), not after. The sync algorithm reads the frame
        // duration the IPA caches at start, so a duration set later reaches the sensor but
        // not the algorithm (measured with capture.py, 2026-10-17).
        const auto& info = camera->controls();
        lc::ControlList ctrls;

        // Camera sync: required, so a camera without it is a failure here rather than a pod
        // that never reaches ready and does not say why.
        if (!info.count(lc::controls::rpi::SyncMode.id()))
            throw std::runtime_error("the camera has no rpi::SyncMode control");
        ctrls.set(lc::controls::rpi::SyncMode,
                  sync_mode == "server" ? lc::controls::rpi::SyncModeServer : lc::controls::rpi::SyncModeClient);
        std::cout << "capture: sync leader is '" << sync_leader << "'; this pod (" << node << ") is " << sync_mode
                  << std::endl;

        // A FIXED frame duration, which the sync algorithm needs: with a range it logs an
        // error every frame and its single-frame correction has nothing stable to correct.
        // Clamped to what the mode allows, and the log says what that was.
        std::optional<int64_t> pinned_us;
        if (auto it = info.find(lc::controls::FrameDurationLimits.id()); it != info.end()) {
            const int64_t lo = it->second.min().get<int64_t>();
            const int64_t hi = it->second.max().get<int64_t>();
            pinned_us = std::clamp(frame_duration_us, lo, hi);
            const std::array<int64_t, 2> pin{*pinned_us, *pinned_us};
            ctrls.set(lc::controls::FrameDurationLimits, std::span<const int64_t, 2>(pin));
            std::cout << "capture: frame duration pinned to " << *pinned_us << " us = " << 1e6 / *pinned_us << " fps";
            if (*pinned_us != frame_duration_us)
                std::cout << " (asked " << frame_duration_us << ", mode allows " << lo << ".." << hi << ")";
            std::cout << std::endl;
        } else {
            std::cout << "capture: WARNING camera reports no FrameDurationLimits, so the frame duration cannot be "
                         "pinned and sync will run against a variable rate"
                      << std::endl;
        }

        // The shutter cap: pin the exposure time and leave gain to the AEGC, which is "cap
        // the shutter, trade to gain". libcamera has no maximum-AE-exposure control.
        std::optional<int32_t> exposure_cap;
        if (max_exposure_us > 0) {
            if (info.count(lc::controls::ExposureTimeMode.id()) && info.count(lc::controls::AnalogueGainMode.id())) {
                ctrls.set(lc::controls::ExposureTimeMode, lc::controls::ExposureTimeModeManual);
                ctrls.set(lc::controls::ExposureTime, max_exposure_us);
                ctrls.set(lc::controls::AnalogueGainMode, lc::controls::AnalogueGainModeAuto);
                exposure_cap = max_exposure_us;
                std::cout << "capture: exposure pinned to " << max_exposure_us << " us, gain left on AEGC"
                          << std::endl;
            } else {
                std::cout << "capture: WARNING the camera has no exposure/gain mode split, so the shutter is not "
                             "capped -- expect motion blur in flight; check the sidecar exposure_us"
                          << std::endl;
            }
        }

        // Focus. A module with no LensPosition control has a mechanically fixed lens: its
        // position is known by construction and nothing can displace it, so that is not a
        // failure. A voice coil is held at the commanded position with AF off.
        json focus_commanded;
        if (!info.count(lc::controls::LensPosition.id())) {
            focus_commanded = "mechanically-fixed";
            std::cout << "capture: no LensPosition control -- this module's lens is mechanically fixed. "
                         "CAMPOD_STILL_FOCUS="
                      << focus << " is ignored." << std::endl;
        } else {
            if (info.count(lc::controls::AfMode.id())) ctrls.set(lc::controls::AfMode, lc::controls::AfModeManual);
            ctrls.set(lc::controls::LensPosition, dioptres);
            focus_commanded = dioptres;
            std::cout << "capture: lens fixed at " << dioptres << " dioptres ("
                      << (dioptres <= 0 ? std::string("infinity") : std::to_string(1.0 / dioptres) + " m")
                      << "), AF off" << std::endl;
        }

        Completed completed;
        camera->requestCompleted.connect(&completed, &Completed::on_complete);
        if (camera->start(&ctrls) != 0) throw std::runtime_error("the camera did not start");
        for (auto& r : requests)
            if (camera->queueRequest(r.get()) != 0) throw std::runtime_error("cannot queue a request");

        const unsigned w = sc.size.width, h = sc.size.height;
        std::cout << "capture: node=" << node << " dir=" << session_dir.string() << " size=" << w << "x" << h
                  << " stride=" << sc.stride << " every frame at the pinned rate, no selection quality=" << quality
                  << " exposure_cap=" << (exposure_cap ? std::to_string(*exposure_cap) : "none")
                  << " focus=" << focus_commanded.dump() << " sync=" << sync_mode
                  << " frame_duration=" << (pinned_us ? std::to_string(*pinned_us) : "unpinned")
                  << " buffers=" << requests.size() << std::endl;

        session.begin(node);
        Jpeg jpeg(quality);
        bool probed = false;
        bool sync_seen = false;
        int64_t sync_waits = 0, unstamped = 0;
        int exit_code = 0;

        try {
            while (!g_stop) {
                lc::Request* r = completed.pop(std::chrono::milliseconds(100));
                session.tick(mono_s());
                if (!r) continue;
                if (r->status() == lc::Request::RequestCancelled) continue;

                const auto& md = r->metadata();
                const lc::FrameBuffer* buf = r->findBuffer(stream);
                const auto ts = md.get(lc::controls::SensorTimestamp);
                const auto sync_ready = md.get(lc::controls::rpi::SyncReady);
                const auto sync_timer = md.get(lc::controls::rpi::SyncTimer);

                // SyncReady stays true for the rest of the session once set, so it is
                // latched. SyncTimer at the crossing is how closely the client believes it
                // is synchronised with the server: the one frame where that reading means
                // anything.
                if (!sync_seen && sync_ready.value_or(false)) {
                    sync_seen = true;
                    std::cout << "capture: SYNC READY as " << sync_mode << " after " << sync_waits
                              << " waiting frames; SyncTimer at the crossing is " << opt(sync_timer).dump() << " us"
                              << std::endl;
                    session.set_sync_ready(true);
                } else if (!sync_seen) {
                    if (++sync_waits == 1 || sync_waits % 50 == 0)
                        std::cout << "capture: waiting for sync as " << sync_mode << ", " << sync_waits
                                  << " frames so far, SyncTimer " << opt(sync_timer).dump() << " us" << std::endl;
                    session.set_sync_ready(false);
                }

                if (!ts) {
                    // No timestamp, nothing to pair with the other pod's frame.
                    if (++unstamped == 1 || unstamped % 50 == 0)
                        std::cout << "capture: " << unstamped
                                  << " frames with no SensorTimestamp, dropped -- cannot place them in time"
                                  << std::endl;
                    session.set_status("unstamped", unstamped);
                } else {
                    // SensorTimestamp is CLOCK_BOOTTIME at the start of exposure (the first
                    // row), the instant camera sync aligns. Session wants it on
                    // CLOCK_MONOTONIC, and the sidecar keeps the boot value as well.
                    const auto clk = capture::clock_triple();
                    const int64_t exposure_wall_ns = *ts + (clk.wall_ns - clk.boot_ns);
                    capture::Frame f;
                    f.kind = "still";
                    f.device_seq = buf->metadata().sequence;
                    f.monotonic_ns = *ts - (clk.boot_ns - clk.mono_ns);
                    f.width = w;
                    f.height = h;
                    f.fields = {
                        {"sensor_timestamp_ns", *ts},
                        // libcamera's own CLOCK_REALTIME stamp for the same instant, and
                        // the value the sync algorithm compares across pods: the cross-pod
                        // field, and the one to prefer if it and exposure_wall_ns disagree.
                        {"frame_wall_clock_ns", opt(md.get(lc::controls::FrameWallClock))},
                        {"exposure_wall_ns", exposure_wall_ns},
                        {"second_offset_us", campod::second_offset_ns(exposure_wall_ns) / 1000},
                        {"exposure_us", opt(md.get(lc::controls::ExposureTime))},
                        {"analogue_gain", opt(md.get(lc::controls::AnalogueGain))},
                        {"digital_gain", opt(md.get(lc::controls::DigitalGain))},
                        {"exposure_cap_us", opt(exposure_cap)},
                        {"lens_position", opt(md.get(lc::controls::LensPosition))},
                        {"af_state", opt(md.get(lc::controls::AfState))},
                        {"focus_commanded", focus_commanded},
                        {"sync_mode", sync_mode},
                        {"sync_ready", opt(sync_ready)},
                        {"sync_timer_us", opt(sync_timer)},
                        {"frame_duration_us", opt(md.get(lc::controls::FrameDuration))},
                        {"size", {w, h}},
                    };
                    const Planes& planes = mappings.at(buf);
                    auto encode = [&] { return jpeg.encode(planes, w, h); };

                    // The first frame exercises the encode and a card write into the
                    // session directory before anyone can arm, so the first armed frame
                    // is not also the first time either runs.
                    if (!probed) {
                        probed = true;
                        const auto bytes = encode();
                        const auto probe = session_dir / ".warmup-probe.jpg";
                        const bool wrote = capture::durable_write(probe.string(), bytes.data(), bytes.size());
                        fs::remove(probe);
                        std::cout << "capture: warmup exercised the encode and " << (wrote ? "a" : "a FAILED")
                                  << " card write (" << w * h * 3 / 2 / 1024 << " kB in, " << bytes.size() / 1024
                                  << " kB out)" << std::endl;
                    }
                    session.frame(f, ".jpg", encode);
                }

                // Encoded from the camera's own buffer, so it goes back only now.
                r->reuse(lc::Request::ReuseBuffers);
                camera->queueRequest(r);
            }
        } catch (const std::exception& e) {
            session.fail(e.what());
            exit_code = 1;
        }

        camera->stop();
        camera->requestCompleted.disconnect(&completed);
        session.stop();
        requests.clear();
        allocator.free(stream);
        camera->release();
        return exit_code;
    } catch (const std::exception& e) {
        session.fail(e.what());
        if (camera) {
            camera->stop();
            camera->release();
        }
        return 1;
    }
}
