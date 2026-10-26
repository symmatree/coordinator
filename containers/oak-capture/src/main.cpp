// oak-capture: colour stills, both mono cameras and the IMU from one OAK, in one process.
//
// One process because the device cannot be shared: whoever opens it owns every sensor on
// it. The colour camera gives the rolling-shutter image; the two monos are global-shutter
// and run at the same rate, so a shadow line in a mono frame is a reference for how far
// the same line bent in the colour frame -- the vibration measurement. The IMU is the
// OAK's own, on the same board as the sensors.
//
// Gating, status and session layout are the campods' (src/conventions.hpp): every frame is
// retrieved whether or not we are capturing, so the cameras stay warm, and only an armed
// gate writes anything. See README.md for the files and fields.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <thread>

#include <depthai/depthai.hpp>
#include <nlohmann/json.hpp>

#include "conventions.hpp"
#include "durable_writer.hpp"

namespace fs = std::filesystem;
using json = nlohmann::json;

namespace {

std::atomic<bool> g_stop{false};

void request_stop(int) { g_stop = true; }

int env_int(const char* name, int fallback) {
    const char* v = std::getenv(name);
    return (v && *v) ? std::atoi(v) : fallback;
}

double env_double(const char* name, double fallback) {
    const char* v = std::getenv(name);
    return (v && *v) ? std::atof(v) : fallback;
}

template <typename TP>
int64_t ns(const TP& tp) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(tp.time_since_epoch()).count();
}

double mono_s() { return capture::clock_triple().mono_ns / 1e9; }

double round3(double v) { return std::round(v * 1000.0) / 1000.0; }

// Per-stream counts: frames retrieved, and frames written.
struct Stream {
    explicit Stream(const char* k) : kind(k) {}
    const char* kind;
    int64_t seen = 0;
    int64_t kept = 0;
    std::optional<int64_t> last_device_seq;
    int64_t device_seq_gaps = 0;
};

}  // namespace

int main() {
    std::signal(SIGTERM, request_stop);
    std::signal(SIGINT, request_stop);

    const std::string node = capture::node_name("OAK_NODE_NAME", "/etc/host-hostname");
    const fs::path out_dir = std::getenv("OAK_CAPTURE_DIR") ? std::getenv("OAK_CAPTURE_DIR") : "/captures";
    const float fps = static_cast<float>(env_double("OAK_FPS", 1.0));
    const int max_exposure_us = env_int("OAK_MAX_EXPOSURE_US", 1000);
    const int jpeg_quality = env_int("OAK_JPEG_QUALITY", 90);
    const int accel_hz = env_int("OAK_IMU_ACCEL_HZ", 480);
    const int gyro_hz = env_int("OAK_IMU_GYRO_HZ", 400);
    const int warmup_frames = env_int("OAK_WARMUP_FRAMES", 10);
    const auto flag_path = capture::env_path("OAK_CAPTURE_FLAG", "/ipc/fc_armed");
    const auto status_path = capture::env_path("OAK_STATUS_FILE", "/ipc/oak_capture_status");

    // The session is the boot id, so this process, the router's timesync.jsonl and
    // vehicle.tlog all land in one directory: captures/<node>/<boot-id>/.
    const std::string session = capture::boot_id();
    const fs::path session_dir = out_dir / node / session;
    fs::create_directories(session_dir);
    capture::copy_manifests(session_dir, {{"/etc/container-image", ""}, {"/etc/fleet-image", "fleet-image"}});

    // The campods' status document, so pod-link publishes it the same way and the
    // coordinator's `ready` means what a pod's does: will arming now produce frames.
    // No sync here, so ready is warm: every stream has delivered its warmup frames.
    json state = {
        {"node", node},
        {"session", session},
        {"camera", "waiting"},
        {"phase", "startup"},
        {"ready", false},
        {"ready_at_mono_s", nullptr},
        {"warm", false},
        {"warm_at_mono_s", nullptr},
        {"capturing", false},
        {"frames", 0},
        {"last_frame_mono_s", nullptr},
    };
    double last_publish = 0;
    auto publish = [&] {
        state["ready"] = state["warm"].get<bool>();
        if (state["ready"].get<bool>() && state["ready_at_mono_s"].is_null()) state["ready_at_mono_s"] = round3(mono_s());
        last_publish = mono_s();
        state["as_of_mono_s"] = round3(last_publish);
        capture::write_status(status_path, state);
    };
    publish();

    // Wait for the device rather than failing: the OAK may be powered after the
    // coordinator, and a process that exits here stays dead until the next boot.
    int waits = 0;
    while (!g_stop && dai::Device::getAllAvailableDevices().empty()) {
        if (waits++ % 30 == 0) std::cout << "oak-capture: waiting for an OAK on USB" << std::endl;
        publish();
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    if (g_stop) {
        state["camera"] = "stopped-while-waiting";
        publish();
        return 0;
    }

    auto device = std::make_shared<dai::Device>();
    const std::string mxid = device->getDeviceId();
    state["camera"] = "present";
    state["device_id"] = mxid;
    publish();
    {
        std::cout << "oak-capture: " << device->getProductName() << " " << mxid << " ("
                  << device->getDeviceName() << "), usb " << static_cast<int>(device->getUsbSpeed())
                  << ", imu " << device->getConnectedIMU() << " fw "
                  << device->getIMUFirmwareVersion().toString() << ", cameras:";
        for (const auto& f : device->getConnectedCameraFeatures()) {
            std::cout << " " << dai::toString(f.socket) << "=" << f.sensorName;
        }
        std::cout << std::endl;
    }

    dai::Pipeline pipeline(device);

    // Colour: full sensor resolution, encoded to JPEG on the camera.
    //
    // A CAP, NOT A FIXED SHUTTER. DepthAI has manual exposure (time AND ISO fixed) or
    // auto exposure with a ceiling; there is no fixed-time, auto-gain mode like the one
    // the campods use. With the ceiling, AE keeps the shutter at or under it and gains
    // the rest -- and may go shorter in bright light. Every frame records what it got.
    auto color = pipeline.create<dai::node::Camera>()->build(dai::CameraBoardSocket::CAM_A, std::nullopt, fps);
    color->initialControl.setAutoExposureLimit(static_cast<uint32_t>(max_exposure_us));
    auto* color_out = color->requestFullResolutionOutput(dai::ImgFrame::Type::NV12, fps);
    auto encoder = pipeline.create<dai::node::VideoEncoder>()->build(*color_out);
    encoder->setDefaultProfilePreset(fps, dai::VideoEncoderProperties::Profile::MJPEG);
    encoder->setQuality(jpeg_quality);

    // The monos, at the colour's rate and with the same ceiling. Raw 8-bit, written as
    // PGM: lossless, and a frame is ~1 MB.
    auto mono = [&](dai::CameraBoardSocket socket) {
        auto cam = pipeline.create<dai::node::Camera>()->build(socket, std::nullopt, fps);
        cam->initialControl.setAutoExposureLimit(static_cast<uint32_t>(max_exposure_us));
        return cam->requestFullResolutionOutput(dai::ImgFrame::Type::GRAY8, fps);
    };
    auto* left_out = mono(dai::CameraBoardSocket::CAM_B);
    auto* right_out = mono(dai::CameraBoardSocket::CAM_C);

    // The IMU in its own frame, with nothing applied: the raw streams are the sensor's
    // output before DepthAI's alignment or calibration, which is what a vibration
    // spectrum wants.
    auto imu = pipeline.create<dai::node::IMU>();
    imu->enableIMUSensor(dai::IMUSensor::ACCELEROMETER_RAW, accel_hz);
    imu->enableIMUSensor(dai::IMUSensor::GYROSCOPE_RAW, gyro_hz);
    imu->setBatchReportThreshold(5);
    imu->setMaxBatchReports(20);

    // Non-blocking, so a slow host drops the oldest message rather than stalling the
    // device; a drop shows as a gap in the device sequence numbers, which are recorded.
    auto color_q = encoder->out.createOutputQueue(4, false);
    auto left_q = left_out->createOutputQueue(4, false);
    auto right_q = right_out->createOutputQueue(4, false);
    auto imu_q = imu->out.createOutputQueue(64, false);

    pipeline.start();
    std::cout << "oak-capture: node=" << node << " dir=" << session_dir.string() << " fps=" << fps
              << " max_exposure_us=" << max_exposure_us << " jpeg_quality=" << jpeg_quality
              << " imu accel=" << accel_hz << "Hz gyro=" << gyro_hz << "Hz"
              << " gate=" << (flag_path ? flag_path->string() : "none") << std::endl;

    capture::Writer writer(session_dir.string());
    const std::string prefix = mxid + "_" + session;
    // Every frame retrieved, kept or not: what the bench needs to measure how the three
    // cameras' timestamps line up without writing a single image.
    std::FILE* ledger = std::fopen((session_dir / (prefix + ".frames.jsonl")).c_str(), "a");
    std::FILE* imu_log = std::fopen((session_dir / (prefix + ".imu.jsonl")).c_str(), "a");
    if (!ledger || !imu_log) {
        std::cerr << "oak-capture: cannot open the ledger or IMU log in " << session_dir << std::endl;
        return 1;
    }

    Stream s_color{"color"}, s_left{"left"}, s_right{"right"};
    int64_t imu_written = 0;
    std::optional<int32_t> last_accel_seq, last_gyro_seq;
    bool capturing = false;
    bool warm = false;
    double last_gate_check = 0;
    state["phase"] = "warmup";
    publish();

    auto line = [](std::FILE* f, const json& j) {
        const std::string s = j.dump();
        std::fwrite(s.data(), 1, s.size(), f);
        std::fputc('\n', f);
    };

    // Everything common to one frame, colour or mono. Writes the ledger line and returns
    // the sidecar when the frame is to be kept.
    auto examine = [&](Stream& s, int64_t device_seq, int64_t mono_ns, int64_t device_ns,
                       int exposure_us, int iso, unsigned width, unsigned height) -> std::optional<json> {
        const auto clk = capture::clock_triple();
        const int64_t wall_ns = capture::mono_to_wall_ns(mono_ns, clk);
        if (s.last_device_seq && device_seq != *s.last_device_seq + 1) s.device_seq_gaps++;
        s.last_device_seq = device_seq;
        const bool keep = warm && capturing;
        line(ledger, {{"kind", s.kind}, {"device_seq", device_seq}, {"monotonic_ns", mono_ns},
                      {"device_timestamp_ns", device_ns}, {"exposure_us", exposure_us}, {"iso", iso},
                      {"kept", keep}});
        s.seen++;
        if (!keep) return std::nullopt;
        return json{
            {"node", node},
            {"device_id", mxid},
            {"kind", s.kind},
            {"seq", s.kept},
            {"device_seq", device_seq},
            {"width", width},
            {"height", height},
            // DepthAI's host-synced frame time, which it defines as the END of exposure
            // (ImgFrame::getTimestamp(START) subtracts the exposure from it). CLOCK_MONOTONIC,
            // the clock the router's timesync.jsonl pairs with the FC's.
            {"monotonic_ns", mono_ns},
            {"device_timestamp_ns", device_ns},
            {"wall_ns", wall_ns},
            {"wall_clock_unix", wall_ns / 1e9},
            {"wall_clock_utc", capture::utc_iso(wall_ns)},
            {"clk", capture::to_json(clk)},
            {"exposure_us", exposure_us},
            {"iso", iso},
            {"max_exposure_us", max_exposure_us},
        };
    };

    auto keep_file = [&](Stream& s, json sidecar, const char* ext, std::vector<uint8_t>&& bytes) {
        char seqbuf[16];
        std::snprintf(seqbuf, sizeof(seqbuf), "%08lld", static_cast<long long>(s.kept));
        const int64_t wall_ns = sidecar["wall_ns"].get<int64_t>();
        const std::string stem = mxid + "_" + s.kind + "_" + seqbuf + "_" + capture::utc_stem(wall_ns);
        sidecar["file"] = stem + ext;
        capture::Job job;
        job.path = (session_dir / (stem + ext)).string();
        job.bytes = std::move(bytes);
        job.sidecar_path = (session_dir / (stem + ".json")).string();
        job.sidecar = sidecar.dump();
        if (writer.submit(std::move(job))) {
            s.kept++;
            state["frames"] = s_color.kept;
            state["last_frame_mono_s"] = round3(mono_s());
        }
    };

    auto on_mono = [&](Stream& s, const std::shared_ptr<dai::ImgFrame>& f) {
        auto sidecar = examine(s, f->getSequenceNum(), ns(f->getTimestamp()), ns(f->getTimestampDevice()),
                               static_cast<int>(f->getExposureTime().count()), f->getSensitivity(),
                               f->getWidth(), f->getHeight());
        if (!sidecar) return;
        auto data = f->getData();
        keep_file(s, std::move(*sidecar), ".pgm",
                  capture::encode_pgm(data.data(), f->getWidth(), f->getHeight(), f->getStride()));
    };

    int exit_code = 0;
    try {
        while (!g_stop) {
            if (!pipeline.isRunning()) {
                std::cerr << "oak-capture: the pipeline stopped -- device lost?" << std::endl;
                exit_code = 1;
                break;
            }
            const double now = mono_s();
            if (now - last_gate_check >= 0.1) {
                last_gate_check = now;
                if (!warm && s_color.seen >= warmup_frames && s_left.seen >= warmup_frames &&
                    s_right.seen >= warmup_frames) {
                    warm = true;
                    capturing = capture::capture_wanted(flag_path);
                    state["warm"] = true;
                    state["warm_at_mono_s"] = round3(now);
                    state["phase"] = "capture";
                    state["capturing"] = capturing;
                    std::cout << "oak-capture: WARM at " << now << " s, "
                              << (capturing ? "capturing" : "paused") << std::endl;
                    publish();
                } else if (warm) {
                    const bool wanted = capture::capture_wanted(flag_path);
                    if (wanted != capturing) {
                        capturing = wanted;
                        state["capturing"] = capturing;
                        std::cout << "oak-capture: " << (capturing ? "resumed" : "paused") << " at " << now
                                  << " s (" << s_color.kept << " colour frames written)" << std::endl;
                        std::fflush(imu_log);
                        std::fflush(ledger);
                        publish();
                    }
                }
            }

            bool any = false;
            for (const auto& f : color_q->tryGetAll<dai::EncodedFrame>()) {
                any = true;
                auto sidecar = examine(s_color, f->getSequenceNum(), ns(f->getTimestamp()),
                                       ns(f->getTimestampDevice()), static_cast<int>(f->getExposureTime().count()),
                                       f->getSensitivity(), f->getWidth(), f->getHeight());
                if (!sidecar) continue;
                (*sidecar)["lens_position"] = f->getLensPosition();
                (*sidecar)["color_temperature_k"] = f->getColorTemperature();
                (*sidecar)["jpeg_quality"] = jpeg_quality;
                auto data = f->getData();
                keep_file(s_color, std::move(*sidecar), ".jpg", std::vector<uint8_t>(data.begin(), data.end()));
            }
            for (const auto& f : left_q->tryGetAll<dai::ImgFrame>()) {
                any = true;
                on_mono(s_left, f);
            }
            for (const auto& f : right_q->tryGetAll<dai::ImgFrame>()) {
                any = true;
                on_mono(s_right, f);
            }
            for (const auto& d : imu_q->tryGetAll<dai::IMUData>()) {
                any = true;
                if (!(warm && capturing)) continue;
                // A packet carries the latest report of each sensor, so at different rates
                // the slower one repeats; its sequence number says which are new.
                for (const auto& p : d->packets) {
                    const auto& a = p.acceleroMeter;
                    if (!last_accel_seq || a.sequence != *last_accel_seq) {
                        last_accel_seq = a.sequence;
                        line(imu_log, {{"t", "a"}, {"seq", a.sequence}, {"monotonic_ns", ns(a.getTimestamp())},
                                       {"device_timestamp_ns", ns(a.getTimestampDevice())},
                                       {"x", a.x}, {"y", a.y}, {"z", a.z}, {"accuracy", static_cast<int>(a.accuracy)}});
                        imu_written++;
                    }
                    const auto& g = p.gyroscope;
                    if (!last_gyro_seq || g.sequence != *last_gyro_seq) {
                        last_gyro_seq = g.sequence;
                        line(imu_log, {{"t", "g"}, {"seq", g.sequence}, {"monotonic_ns", ns(g.getTimestamp())},
                                       {"device_timestamp_ns", ns(g.getTimestampDevice())},
                                       {"x", g.x}, {"y", g.y}, {"z", g.z}, {"accuracy", static_cast<int>(g.accuracy)}});
                        imu_written++;
                    }
                }
            }

            if (now - last_publish >= 1.0) {
                state["frames_seen"] = {{"color", s_color.seen}, {"left", s_left.seen}, {"right", s_right.seen}};
                state["device_seq_gaps"] = {{"color", s_color.device_seq_gaps},
                                            {"left", s_left.device_seq_gaps},
                                            {"right", s_right.device_seq_gaps}};
                state["writer_dropped"] = writer.dropped();
                state["imu_records"] = imu_written;
                publish();
            }
            if (!any) std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
    } catch (const std::exception& e) {
        std::cerr << "oak-capture: " << e.what() << std::endl;
        exit_code = 1;
    }

    pipeline.stop();
    writer.stop();
    std::fclose(ledger);
    std::fclose(imu_log);
    state["phase"] = "stopped";
    state["warm"] = false;
    state["capturing"] = false;
    publish();
    std::cout << "oak-capture: stopped; written color=" << s_color.kept << " left=" << s_left.kept
              << " right=" << s_right.kept << " imu=" << imu_written << ", dropped by the writer "
              << writer.dropped() << std::endl;
    return exit_code;
}
