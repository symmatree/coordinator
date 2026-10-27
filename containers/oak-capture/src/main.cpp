// oak-capture: colour stills, both mono cameras and the IMU from one OAK, in one process.
//
// One process because the device cannot be shared: whoever opens it owns every sensor on
// it. The colour camera gives the rolling-shutter image; the two monos are global-shutter
// and run at the same rate, so a shadow line in a mono frame is a reference for how far
// the same line bent in the colour frame -- the vibration measurement. The IMU is the
// OAK's own, on the same board as the sensors.
//
// This file is the depthai half: the device, the pipeline and reading each message. The
// control loop -- warmup, the arm gate, the ledger, what is written and the status -- is
// capture::Session (containers/capture-common), shared with any other camera backend.
// See README.md for the files and fields.
#include <atomic>
#include <chrono>
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
#include "session.hpp"

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

// What every OAK frame carries, colour or mono.
template <typename F>
capture::Frame oak_frame(const char* kind, const F& f, const std::string& device_id, int max_exposure_us) {
    capture::Frame out;
    out.kind = kind;
    out.device_seq = f.getSequenceNum();
    // DepthAI's host-synced frame time, which it defines as the END of exposure
    // (ImgFrame::getTimestamp(START) subtracts the exposure from it). CLOCK_MONOTONIC, the
    // clock the router's timesync.jsonl pairs with the FC's.
    out.monotonic_ns = ns(f.getTimestamp());
    out.width = f.getWidth();
    out.height = f.getHeight();
    out.fields = {
        {"device_id", device_id},
        {"device_timestamp_ns", ns(f.getTimestampDevice())},
        {"exposure_us", static_cast<int>(f.getExposureTime().count())},
        {"iso", f.getSensitivity()},
        {"max_exposure_us", max_exposure_us},
    };
    return out;
}

}  // namespace

int main() {
    std::signal(SIGTERM, request_stop);
    std::signal(SIGINT, request_stop);

    const std::string node = capture::node_name("OAK_NODE_NAME", "/etc/host-hostname");
    const fs::path out_dir = std::getenv("OAK_CAPTURE_DIR") ? std::getenv("OAK_CAPTURE_DIR") : "/captures";
    const float fps = static_cast<float>(env_double("OAK_FPS", 2.0));
    const int max_exposure_us = env_int("OAK_MAX_EXPOSURE_US", 1000);
    const int jpeg_quality = env_int("OAK_JPEG_QUALITY", 90);
    const int accel_hz = env_int("OAK_IMU_ACCEL_HZ", 480);
    const int gyro_hz = env_int("OAK_IMU_GYRO_HZ", 400);
    const auto flag_path = capture::env_path("OAK_CAPTURE_FLAG", "/ipc/fc_armed");

    // The session is the boot id, so this process, the router's timesync.jsonl and
    // vehicle.tlog all land in one directory: captures/<node>/<boot-id>/.
    const std::string session_id = capture::boot_id();
    const fs::path session_dir = out_dir / node / session_id;
    fs::create_directories(session_dir);
    capture::copy_manifests(session_dir, {{"/etc/container-image", ""}, {"/etc/fleet-image", "fleet-image"}});

    capture::Session session({
        .node = node,
        .session = session_id,
        .dir = session_dir,
        .streams = {"color", "left", "right"},
        .warmup_frames = env_int("OAK_WARMUP_FRAMES", 10),
        .flag_path = flag_path,
        .status_path = capture::env_path("OAK_STATUS_FILE", "/ipc/oak_capture_status"),
        .log_name = "oak-capture",
    });
    session.publish();

    // Wait for the device rather than failing: the OAK may be powered after the
    // coordinator, and a process that exits here stays dead until the next boot.
    int waits = 0;
    while (!g_stop && dai::Device::getAllAvailableDevices().empty()) {
        if (waits++ % 30 == 0) std::cout << "oak-capture: waiting for an OAK on USB" << std::endl;
        session.publish();
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    if (g_stop) {
        session.set_status("camera", "stopped-while-waiting");
        session.publish();
        return 0;
    }

    // Anything thrown from here to the loop is a setup failure -- a rate the sensor will not
    // run at, a device that went away while opening -- and is published as `failed` with its
    // message, rather than leaving the last status document standing.
    try {
        auto device = std::make_shared<dai::Device>();
        const std::string mxid = device->getDeviceId();
        session.set_status("camera", "present");
        session.set_status("device_id", mxid);
        session.publish();
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
        // Fixed focus: AF off, the lens held where it was when the device was calibrated, so
        // the stored colour intrinsics describe every frame.
        const uint8_t lens_position = device->readCalibration().getLensPosition(dai::CameraBoardSocket::CAM_A);
        color->initialControl.setAutoFocusMode(dai::CameraControl::AutoFocusMode::OFF);
        color->initialControl.setManualFocus(lens_position);
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
                  << " lens_position=" << static_cast<int>(lens_position) << " (calibration, AF off)"
                  << " imu accel=" << accel_hz << "Hz gyro=" << gyro_hz << "Hz"
                  << " gate=" << (flag_path ? flag_path->string() : "none") << std::endl;

        session.begin(mxid);
        std::FILE* imu_log = std::fopen((session_dir / (mxid + "_" + session_id + ".imu.jsonl")).c_str(), "a");
        if (!imu_log) throw std::runtime_error("cannot open the IMU log in " + session_dir.string());
        int64_t imu_written = 0;
        std::optional<int32_t> last_accel_seq, last_gyro_seq;
        auto imu_line = [&](const char* t, const auto& r) {
            const std::string s = json{{"t", t},
                                       {"seq", r.sequence},
                                       {"monotonic_ns", ns(r.getTimestamp())},
                                       {"device_timestamp_ns", ns(r.getTimestampDevice())},
                                       {"x", r.x},
                                       {"y", r.y},
                                       {"z", r.z},
                                       {"accuracy", static_cast<int>(r.accuracy)}}
                                      .dump();
            std::fwrite(s.data(), 1, s.size(), imu_log);
            std::fputc('\n', imu_log);
            imu_written++;
        };
        auto on_mono = [&](const char* kind, const std::shared_ptr<dai::ImgFrame>& f) {
            session.frame(oak_frame(kind, *f, mxid, max_exposure_us), ".pgm", [&] {
                auto data = f->getData();
                return capture::encode_pgm(data.data(), f->getWidth(), f->getHeight(), f->getStride());
            });
        };

        int exit_code = 0;
        bool was_capturing = false;
        try {
            while (!g_stop) {
                if (!pipeline.isRunning()) {
                    session.fail("the pipeline stopped -- device lost?");
                    exit_code = 1;
                    break;
                }
                session.tick(mono_s());
                if (was_capturing && !session.capturing()) std::fflush(imu_log);
                was_capturing = session.capturing();

                bool any = false;
                for (const auto& f : color_q->tryGetAll<dai::EncodedFrame>()) {
                    any = true;
                    auto frame = oak_frame("color", *f, mxid, max_exposure_us);
                    frame.fields["lens_position"] = f->getLensPosition();
                    frame.fields["color_temperature_k"] = f->getColorTemperature();
                    frame.fields["jpeg_quality"] = jpeg_quality;
                    session.frame(frame, ".jpg", [&] {
                        auto data = f->getData();
                        return std::vector<uint8_t>(data.begin(), data.end());
                    });
                }
                for (const auto& f : left_q->tryGetAll<dai::ImgFrame>()) {
                    any = true;
                    on_mono("left", f);
                }
                for (const auto& f : right_q->tryGetAll<dai::ImgFrame>()) {
                    any = true;
                    on_mono("right", f);
                }
                for (const auto& d : imu_q->tryGetAll<dai::IMUData>()) {
                    any = true;
                    if (!session.capturing()) continue;
                    // A packet carries the latest report of each sensor, so at different rates
                    // the slower one repeats; its sequence number says which are new.
                    for (const auto& p : d->packets) {
                        if (!last_accel_seq || p.acceleroMeter.sequence != *last_accel_seq) {
                            last_accel_seq = p.acceleroMeter.sequence;
                            imu_line("a", p.acceleroMeter);
                        }
                        if (!last_gyro_seq || p.gyroscope.sequence != *last_gyro_seq) {
                            last_gyro_seq = p.gyroscope.sequence;
                            imu_line("g", p.gyroscope);
                        }
                    }
                }
                session.set_status("imu_records", imu_written);
                if (!any) std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
        } catch (const std::exception& e) {
            session.fail(e.what());
            exit_code = 1;
        }

        pipeline.stop();
        std::fclose(imu_log);
        session.stop();
        std::cout << "oak-capture: imu records written " << imu_written << std::endl;
        return exit_code;
    } catch (const std::exception& e) {
        session.fail(e.what());
        return 1;
    }
}
