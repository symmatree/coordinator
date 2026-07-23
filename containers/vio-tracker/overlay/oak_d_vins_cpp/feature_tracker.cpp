#include <iostream>
#include <thread>
#include <chrono>

#include <arpa/inet.h>
#include <errno.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <unistd.h>
#include <string.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/un.h>
#include <signal.h>

#include <opencv2/calib3d.hpp>
#include <opencv2/imgcodecs.hpp>  // #72: cv::imwrite for disparity/still capture
#include <opencv2/imgproc.hpp>    // #72: cv::cvtColor (NV12 -> BGR for stills)
#include <ctime>
#include <cstdlib>  // #72: getenv/atof/atoi
#include <cstdint>  // #78: uint16_t/uint32_t for the .feat frame header
#include <memory>   // #89: unique_ptr for the capture writer

#include "capture_writer.hpp"  // #89: power-loss-safe background writer for captures

// Includes common necessary includes for development using depthai library
#include "depthai/depthai.hpp"
#include "deque"
#include "unordered_map"
#include "unordered_set"

#define CAM_W 640
#define CAM_H 400
#define PAIR_DIST_SQ 9

struct MyPoint2d {
    double x = 0;
    double y = 0;
    MyPoint2d() {}
    MyPoint2d(double px, double py) {
        x = px;
        y = py;
    }
};

double big_buf[12*1024/sizeof(double)];
bool gogogo = true;

void sig_func(int sig) {
    gogogo = false;
}

void calc_rect_cam_intri(dai::CalibrationHandler calibData, double* f, double* cx, double* cy) {
    auto l_intrinsics = calibData.getCameraIntrinsics(dai::CameraBoardSocket::CAM_B, 640, 400);
    float data[9];
    int i = -1;
    for (auto row : l_intrinsics) {
        for (auto val : row) {
            data[++i] = val;
        }
    }
    cv::Mat l_m = cv::Mat(3, 3, CV_32FC1, data);

    auto r_intrinsics = calibData.getCameraIntrinsics(dai::CameraBoardSocket::CAM_C, 640, 400);
    i = -1;
    for (auto row : r_intrinsics) {
        for (auto val : row) {
            data[++i] = val;
        }
    }
    cv::Mat r_m = cv::Mat(3, 3, CV_32FC1, data);

    auto l_d = calibData.getDistortionCoefficients(dai::CameraBoardSocket::CAM_B);
    auto r_d = calibData.getDistortionCoefficients(dai::CameraBoardSocket::CAM_C);
    auto extrinsics = calibData.getCameraExtrinsics(dai::CameraBoardSocket::CAM_B, dai::CameraBoardSocket::CAM_C);

    cv::Mat r = (cv::Mat_<double>(3,3) << extrinsics[0][0], extrinsics[0][1], extrinsics[0][2], extrinsics[1][0], extrinsics[1][1], extrinsics[1][2], extrinsics[2][0], extrinsics[2][1], extrinsics[2][2]);
    cv::Mat t = (cv::Mat_<double>(3,1) << extrinsics[0][3], extrinsics[1][3], extrinsics[2][3]);
    cv::Mat r1, r2, p1, p2, q;
    cv::stereoRectify(l_m, l_d, r_m, r_d, cv::Size(640, 400), r, t, r1, r2, p1, p2, q, cv::CALIB_ZERO_DISPARITY, 0);

    std::cout << "P1\n" << p1 << "\nP2\n" << p2 << "\n";

    *f = p1.at<double>(0, 0);
    *cx = p1.at<double>(0, 2);
    *cy = p1.at<double>(1, 2);
}

// --- coordinator #72: opt-in capture of periodic disparity + RGB stills to disk. ---
// Guarded by OAK_CAPTURE_DIR; with it unset the tracker behaves exactly as upstream.

static void mkdir_p(const std::string& path) {
    std::string cur;
    for (size_t i = 0; i < path.size(); ++i) {
        cur += path[i];
        if (path[i] == '/' && cur.size() > 1) mkdir(cur.c_str(), 0775);
    }
    mkdir(path.c_str(), 0775);
}

static const char* env_or(const char* name, const char* dflt) {
    const char* v = getenv(name);
    return (v && *v) ? v : dflt;
}

// <node>_<seq:08>_<YYYYmmddTHHMMSS_ffffffZ> -- matches the phase-1 still writer (#73).
static std::string make_stem(const std::string& node, long seq,
                             std::chrono::system_clock::time_point wall) {
    std::time_t tt = std::chrono::system_clock::to_time_t(wall);
    long long us = std::chrono::duration_cast<std::chrono::microseconds>(
                       wall.time_since_epoch()).count() % 1000000;
    struct tm tv;
    gmtime_r(&tt, &tv);
    char b[24];
    strftime(b, sizeof(b), "%Y%m%dT%H%M%S", &tv);
    char out[96];
    snprintf(out, sizeof(out), "%s_%08ld_%s_%06lldZ", node.c_str(), seq, b, us);
    return out;
}

template <typename TP>
static long long ts_ns(const TP& tp) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(tp.time_since_epoch()).count();
}

// Elapsed >= 1/hz since *last? update *last and return true (cadence gate).
static bool due(std::chrono::steady_clock::time_point& last, double hz) {
    auto now = std::chrono::steady_clock::now();
    if (std::chrono::duration<double>(now - last).count() < 1.0 / hz) return false;
    last = now;
    return true;
}

// Phase-1-compatible JSON sidecar (#73). exposure_us < 0 omits exposure/iso (disparity).
// #89: returns the JSON as a string so the background writer persists it durably
// alongside the image (tmp -> fsync -> rename), instead of an un-fsynced fopen here.
static std::string build_sidecar(const std::string& node, long seq,
                                 const std::string& file, const char* kind,
                                 std::chrono::system_clock::time_point wall, long long sensor_ns,
                                 int device_seq, int width, int height, long long exposure_us, int iso) {
    std::time_t tt = std::chrono::system_clock::to_time_t(wall);
    long long us = std::chrono::duration_cast<std::chrono::microseconds>(
                       wall.time_since_epoch()).count() % 1000000;
    struct tm tv;
    gmtime_r(&tt, &tv);
    char b[24], iso_s[40];
    strftime(b, sizeof(b), "%Y-%m-%dT%H:%M:%S", &tv);
    snprintf(iso_s, sizeof(iso_s), "%s.%06lldZ", b, us);
    double wall_unix = std::chrono::duration<double>(wall.time_since_epoch()).count();
    long long mono_ns = ts_ns(std::chrono::steady_clock::now());
    char buf[512];
    int len = snprintf(buf, sizeof(buf),
            "{\"node\":\"%s\",\"seq\":%ld,\"file\":\"%s\",\"kind\":\"%s\","
            "\"wall_clock_utc\":\"%s\",\"wall_clock_unix\":%.6f,\"monotonic_ns\":%lld,"
            "\"sensor_timestamp_ns\":%lld,\"device_seq\":%d,\"width\":%d,\"height\":%d",
            node.c_str(), seq, file.c_str(), kind, iso_s, wall_unix, mono_ns,
            sensor_ns, device_seq, width, height);
    // snprintf returns the would-be length; clamp so a long node name can't make us
    // read past buf (it never approaches 512 B in practice).
    size_t use = 0;
    if (len > 0) use = (size_t)len < sizeof(buf) ? (size_t)len : sizeof(buf) - 1;
    std::string out(buf, use);
    if (exposure_us >= 0) {
        snprintf(buf, sizeof(buf), ",\"exposure_us\":%lld,\"iso\":%d", exposure_us, iso);
        out += buf;
    }
    out += "}\n";
    return out;
}

// --- coordinator #78: tee the estimator's raw input datagrams (chobits_imu +
// chobits_features) to a .feat file in the SAME framed format vio-ipc-record writes, so a
// flight with the estimator *running* still yields a fixture replayable through vins_fusion
// offline (input_replayer, #35). #42 captures the same streams with the estimator off; here
// the tracker -- the sender -- tees its own output, so there is no socket contention. Same
// OAK_CAPTURE_DIR gate and session dir as the #72 capture above.

static FILE* feat_file = nullptr;                 // open only while capturing
static constexpr uint16_t FEAT_SID_IMU = 0;       // socket ids in the .feat manifest;
static constexpr uint16_t FEAT_SID_FEATURES = 1;  // input_replayer maps them by basename

// Append one framed datagram: <ddHI> little-endian (t_mono, t_unix, socket_id, length) then
// the raw payload bytes -- byte-identical to what sendto() puts on the wire, so the replayer
// re-sends vins its own bytes. Fields are written separately (not as a packed struct) to
// avoid C struct padding; Pi/arm64 and x86 are both little-endian, matching Python's '<'.
// #89: fdatasync the .feat on an interval so a power cut loses at most the last
// FEAT_FSYNC_SEC of records, not the whole libc + page-cache window (the ~30 s tail
// we lost on 260712). The stream is append-only, so a torn final record is benign --
// input_replayer stops cleanly at a truncated tail.
static constexpr double FEAT_FSYNC_SEC = 1.0;
static std::chrono::steady_clock::time_point feat_last_sync = std::chrono::steady_clock::now();

static void feat_tee(uint16_t socket_id, const void* payload, uint32_t length) {
    if (!feat_file) return;
    double t_mono = std::chrono::duration<double>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
    double t_unix = std::chrono::duration<double>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    fwrite(&t_mono, sizeof t_mono, 1, feat_file);
    fwrite(&t_unix, sizeof t_unix, 1, feat_file);
    fwrite(&socket_id, sizeof socket_id, 1, feat_file);
    fwrite(&length, sizeof length, 1, feat_file);
    if (length) fwrite(payload, 1, length, feat_file);
    auto now = std::chrono::steady_clock::now();
    if (std::chrono::duration<double>(now - feat_last_sync).count() >= FEAT_FSYNC_SEC) {
        fflush(feat_file);
        fdatasync(fileno(feat_file));
        feat_last_sync = now;
    }
}

int main(int argc, char **argv) {
    bool imu_ok = false;
    int ccc=0;
    enum DEV_TYPE {OAK_D, OAK_D_PRO} dev_type;

    struct sigaction act;
    memset(&act, 0, sizeof(act));
    act.sa_handler = sig_func;
    sigaction(SIGINT, &act, NULL);
    sigaction(SIGTERM, &act, NULL);  // #72: clean shutdown on `docker stop` (SIGTERM)

    struct sockaddr_un ipc_local_addr, imu_addr, features_addr;
    memset(&ipc_local_addr, 0, sizeof(struct sockaddr_un));
    ipc_local_addr.sun_family = AF_UNIX;
    strcpy(ipc_local_addr.sun_path, "/tmp/chobits_2222");
    int ipc_sock = socket(AF_UNIX, SOCK_DGRAM, 0);
    unlink("/tmp/chobits_2222");
    bind(ipc_sock, (struct sockaddr*)&ipc_local_addr, sizeof(ipc_local_addr));
    memset(&imu_addr, 0, sizeof(struct sockaddr_un));
    imu_addr.sun_family = AF_UNIX;
    strcpy(imu_addr.sun_path, "/tmp/chobits_imu");
    memset(&features_addr, 0, sizeof(struct sockaddr_un));
    features_addr.sun_family = AF_UNIX;
    strcpy(features_addr.sun_path, "/tmp/chobits_features");

    // #72: capture config (opt-in). Unset OAK_CAPTURE_DIR => no color cam, no disk writes.
    const char* capture_dir = getenv("OAK_CAPTURE_DIR");
    bool capture = capture_dir && *capture_dir;
    std::string node = env_or("OAK_NODE_NAME", "");
    if (node.empty()) { char hn[256]; gethostname(hn, sizeof(hn)); node = hn; }
    double disp_hz = atof(env_or("OAK_DISPARITY_HZ", "1.0"));
    double still_hz = atof(env_or("OAK_STILL_HZ", "0.2"));
    int jpeg_q = atoi(env_or("OAK_JPEG_QUALITY", "92"));
    std::string res_key = env_or("OAK_STILL_RESOLUTION", "12mp");
    auto still_res = dai::ColorCameraProperties::SensorResolution::THE_12_MP;
    if (res_key == "4k") still_res = dai::ColorCameraProperties::SensorResolution::THE_4_K;
    else if (res_key == "1080p") still_res = dai::ColorCameraProperties::SensorResolution::THE_1080_P;
    if (capture) std::cout << "capture: enabled (node=" << node << ", res=" << res_key << ")\n";

    // Create pipeline
    dai::Pipeline pipeline;

    // Define sources and outputs
    auto monoLeft = pipeline.create<dai::node::MonoCamera>();
    auto monoRight = pipeline.create<dai::node::MonoCamera>();
    auto featureTrackerLeft = pipeline.create<dai::node::FeatureTracker>();
    auto featureTrackerRight = pipeline.create<dai::node::FeatureTracker>();
    auto imu = pipeline.create<dai::node::IMU>();

    auto xoutTrackedFeaturesLeft = pipeline.create<dai::node::XLinkOut>();
    auto xoutTrackedFeaturesRight = pipeline.create<dai::node::XLinkOut>();
    auto depth = pipeline.create<dai::node::StereoDepth>();
    auto xout_disp = pipeline.create<dai::node::XLinkOut>();
    auto xout_imu = pipeline.create<dai::node::XLinkOut>();

    xoutTrackedFeaturesLeft->setStreamName("trackedFeaturesLeft");
    xoutTrackedFeaturesRight->setStreamName("trackedFeaturesRight");
    xout_disp->setStreamName("disparity");
    xout_imu->setStreamName("imu");

    // Properties
    monoLeft->setResolution(dai::MonoCameraProperties::SensorResolution::THE_400_P);
    monoLeft->setCamera("left");
    monoLeft->setFps(20);
    monoRight->setResolution(dai::MonoCameraProperties::SensorResolution::THE_400_P);
    monoRight->setCamera("right");
    monoRight->setFps(20);

    featureTrackerLeft->initialConfig.setNumTargetFeatures(16*5);
    featureTrackerRight->initialConfig.setNumTargetFeatures(16*5);
    /*dai::RawFeatureTrackerConfig config = featureTrackerLeft->initialConfig.get();
    config.cornerDetector.numMaxFeatures = 100;
    featureTrackerLeft->initialConfig.set(config);
    config = featureTrackerRight->initialConfig.get();
    config.cornerDetector.numMaxFeatures = 100;
    featureTrackerRight->initialConfig.set(config);*/
    // By default the least mount of resources are allocated
    // increasing it improves performance when optical flow is enabled
    featureTrackerLeft->setHardwareResources(2, 2);
    featureTrackerRight->setHardwareResources(2, 2);

    depth->setDefaultProfilePreset(dai::node::StereoDepth::PresetMode::HIGH_ACCURACY);
    depth->initialConfig.setMedianFilter(dai::MedianFilter::KERNEL_5x5);
    depth->setLeftRightCheck(true);
    depth->setExtendedDisparity(false);
    depth->setSubpixel(false);
    depth->setDepthAlign(dai::RawStereoDepthConfig::AlgorithmControl::DepthAlign::RECTIFIED_LEFT);
    depth->setAlphaScaling(0);

    // enable ACCELEROMETER_RAW at 500 hz rate
    imu->enableIMUSensor(dai::IMUSensor::ACCELEROMETER, 125);
    // enable GYROSCOPE_RAW at 400 hz rate
    imu->enableIMUSensor(dai::IMUSensor::GYROSCOPE_CALIBRATED, 100);
    // it's recommended to set both setBatchReportThreshold and setMaxBatchReports to 20 when integrating in a pipeline with a lot of input/output connections
    // above this threshold packets will be sent in batch of X, if the host is not blocked and USB bandwidth is available
    imu->setBatchReportThreshold(1);
    // maximum number of IMU packets in a batch, if it's reached device will block sending until host can receive it
    // if lower or equal to batchReportThreshold then the sending is always blocking on device
    // useful to reduce device's CPU load  and number of lost packets, if CPU load is high on device side due to multiple nodes
    imu->setMaxBatchReports(10);

    // Linking
    monoLeft->out.link(depth->left);
    depth->rectifiedLeft.link(featureTrackerLeft->inputImage);
    featureTrackerLeft->outputFeatures.link(xoutTrackedFeaturesLeft->input);

    monoRight->out.link(depth->right);
    depth->rectifiedRight.link(featureTrackerRight->inputImage);
    featureTrackerRight->outputFeatures.link(xoutTrackedFeaturesRight->input);

    depth->disparity.link(xout_disp->input);
    imu->out.link(xout_imu->input);

    // #72: color-still branch on the SAME pipeline, only when capturing. Raw still to host +
    // host-side cv::imwrite (no on-device encoder: 12 MP exceeds the MJPEG encoder limits, and
    // host encode preserves exposure/ISO metadata). Triggered on cadence via stillControl.
    if (capture) {
        auto colorCam = pipeline.create<dai::node::ColorCamera>();
        colorCam->setBoardSocket(dai::CameraBoardSocket::CAM_A);  // RGB / center camera
        colorCam->setResolution(still_res);
        // Still image-quality controls to fight in-flight motion blur (vio-quality E18: in flight the
        // stills smeared -- auto-exposure ran to ~30 ms @ ISO 110, leaving ~4 stops of gain unused).
        //  - OAK_STILL_MAX_EXPOSURE_US: cap the auto-exposure shutter so AE trades to ISO/gain instead
        //    of a long exposure (accept some noise/underexposure for a sharp frame). 0 = no cap (auto).
        //  - OAK_STILL_FOCUS: fix the lens (0-255 lens position, AF off) to stop autofocus hunting;
        //    "auto" = leave AF on. Calibrate the value on the bench (sweep the position, pick the
        //    var-of-Laplacian peak at flight distance -- analysis/image-sharpness-vs-motion tooling).
        int max_exp_us = atoi(env_or("OAK_STILL_MAX_EXPOSURE_US", "0"));
        if (max_exp_us > 0) {
            colorCam->initialControl.setAutoExposureLimit((uint32_t)max_exp_us);
            std::cout << "capture: still exposure capped at " << max_exp_us << " us (AE -> ISO/gain)\n";
        }
        std::string focus_s = env_or("OAK_STILL_FOCUS", "auto");
        if (focus_s != "auto" && !focus_s.empty()) {
            int lp = atoi(focus_s.c_str()); lp = lp < 0 ? 0 : (lp > 255 ? 255 : lp);
            colorCam->initialControl.setManualFocus((uint8_t)lp);
            std::cout << "capture: still fixed focus lensPos=" << lp << " (AF off)\n";
        }
        auto xout_still = pipeline.create<dai::node::XLinkOut>();
        xout_still->setStreamName("still");
        colorCam->still.link(xout_still->input);
        auto xin_ctrl = pipeline.create<dai::node::XLinkIn>();
        xin_ctrl->setStreamName("stillControl");
        xin_ctrl->out.link(colorCam->inputControl);
    }

    // Connect to device and start pipeline (UsbSpeed::HIGH -- was a Dockerfile sed, now here)
    dai::Device device(pipeline, dai::UsbSpeed::HIGH);

    std::cout << "Usb speed: " << device.getUsbSpeed() << "\n";
    std::cout << "Device name: " << device.getDeviceName() << " Product name: " << device.getProductName() << "\n";
    if (device.getDeviceName() == "OAK-D") dev_type = OAK_D; else dev_type = OAK_D_PRO;

    dai::CalibrationHandler calibData = device.readCalibration2();
    double f, cx, cy;
    calc_rect_cam_intri(calibData, &f, &cx, &cy);
    double l_inv_k11 = 1.0 / f;
    double l_inv_k13 = -cx / f;
    double l_inv_k22 = 1.0 / f;
    double l_inv_k23 = -cy / f;
    double r_inv_k11 = 1.0 / f;
    double r_inv_k13 = -cx / f;
    double r_inv_k22 = 1.0 / f;
    double r_inv_k23 = -cy / f;

    //device.setLogOutputLevel(dai::LogLevel::DEBUG);
    //device.setLogLevel(dai::LogLevel::DEBUG);

    // Output queues used to receive the results
    auto outputFeaturesLeftQueue = device.getOutputQueue("trackedFeaturesLeft", 1, false);
    auto outputFeaturesRightQueue = device.getOutputQueue("trackedFeaturesRight", 1, false);
    auto disp_queue = device.getOutputQueue("disparity", 1, false);
    auto imuQueue = device.getOutputQueue("imu", 5, false);

    // #72: capture session dir + still queues (only when capturing)
    std::shared_ptr<dai::DataOutputQueue> still_queue;
    std::shared_ptr<dai::DataInputQueue> still_ctrl_queue;
    std::string session_dir;
    std::unique_ptr<capture::Writer> writer;  // #89: durable off-hot-path image writes
    long disp_saved = 0, still_saved = 0;
    auto last_disp_save = std::chrono::steady_clock::now() - std::chrono::hours(1);
    auto last_still_trig = last_disp_save;
    if (capture) {
        std::time_t st = std::time(nullptr);
        struct tm stv; gmtime_r(&st, &stv);
        char sess[24]; strftime(sess, sizeof(sess), "%Y%m%dT%H%M%SZ", &stv);
        session_dir = std::string(capture_dir) + "/" + node + "/" + sess;
        mkdir_p(session_dir);
        writer.reset(new capture::Writer(session_dir, 4));  // #89
        still_queue = device.getOutputQueue("still", 2, false);
        still_ctrl_queue = device.getInputQueue("stillControl");
        std::cout << "capture: dir=" << session_dir << " disp_hz=" << disp_hz
                  << " still_hz=" << still_hz << " jpeg_q=" << jpeg_q << "\n";

        // #78: open the .feat input tee + write its manifest (matches vio-ipc-record v1).
        std::string feat_path = session_dir + "/" + node + "_" + sess + ".feat";
        feat_file = fopen(feat_path.c_str(), "wb");
        if (feat_file) {
            FILE* mf = fopen((feat_path + ".json").c_str(), "w");
            if (mf) {
                double now_unix = std::chrono::duration<double>(
                    std::chrono::system_clock::now().time_since_epoch()).count();
                fprintf(mf,
                    "{\n  \"version\": 1,\n"
                    "  \"frame_format\": \"<ddHI> (t_mono, t_unix, socket_id, length) then <length> raw bytes\",\n"
                    "  \"sockets\": {\"%u\": \"%s\", \"%u\": \"%s\"},\n"
                    "  \"start_unix\": %.6f\n}\n",
                    (unsigned)FEAT_SID_IMU, imu_addr.sun_path,
                    (unsigned)FEAT_SID_FEATURES, features_addr.sun_path, now_unix);
                fclose(mf);
            }
            std::cout << "capture: feat tee -> " << feat_path << "\n";
        } else {
            std::cout << "capture: WARN could not open feat tee " << feat_path << "\n";
        }
    }

    int l_seq = -1, r_seq = -2, disp_seq = -3;
    std::vector<std::uint8_t> disp_frame;
    std::vector<dai::TrackedFeature> l_features, r_features;
    std::map<int, MyPoint2d> l_prv_features, r_prv_features;
    std::map<int, dai::Point2f> r_cur_features;
    std::chrono::time_point<std::chrono::steady_clock, std::chrono::steady_clock::duration> features_tp, prv_features_tp;
    std::map<int, int> lr_id_mapping;

    // Clear queue events
    //jakaskerl suggest remove this line
    //https://discuss.luxonis.com/d/3484-getqueueevent-takes-much-additional-time/7
    //device.getQueueEvents();

    while(gogogo) {
        auto q_name = device.getQueueEvent();

        if (q_name == "trackedFeaturesLeft") {
            auto data = outputFeaturesLeftQueue->get<dai::TrackedFeatures>();
            l_features = data->trackedFeatures;
            l_seq = data->getSequenceNum();
            features_tp = data->getTimestampDevice();
            //std::cout << "l ft " << l_seq << " latency:" << std::chrono::duration<float, std::milli>(std::chrono::steady_clock::now() - features_tp).count() << " ms\n";
        } else if (q_name == "trackedFeaturesRight") {
            auto data = outputFeaturesRightQueue->get<dai::TrackedFeatures>();
            r_features = data->trackedFeatures;
            r_seq = data->getSequenceNum();
            //std::cout << "r ft " << r_seq << " latency:" << std::chrono::duration<float, std::milli>(std::chrono::steady_clock::now() - data->getTimestamp()).count() << " ms\n";
            r_cur_features.clear();
            for (const auto &feature : r_features) {
                r_cur_features[feature.id] = feature.position;
            }
        } else if (q_name == "disparity") {
            auto disp_data = disp_queue->get<dai::ImgFrame>();
            disp_seq = disp_data->getSequenceNum();
            disp_frame = disp_data->getData();
            if (capture && disp_hz > 0 && due(last_disp_save, disp_hz)) {  // #72: save disparity
                auto wall = std::chrono::system_clock::now();
                std::string stem = make_stem(node, disp_saved, wall);
                std::string base = session_dir + "/" + stem;
                cv::Mat dm(CAM_H, CAM_W, CV_8UC1, disp_frame.data());  // 8-bit 640x400
                // #89: encode here (hot path, as before), hand the bytes to the durable
                // background writer so the fsync/rename never stalls feature tracking.
                capture::Job job;
                if (cv::imencode(".png", dm, job.bytes)) {
                    job.path = base + ".png";
                    job.sidecar_path = base + ".json";
                    job.sidecar = build_sidecar(node, disp_saved, stem + ".png", "disparity", wall,
                                                ts_ns(disp_data->getTimestampDevice()), disp_seq,
                                                CAM_W, CAM_H, -1, 0);
                    if (writer->submit(std::move(job))) ++disp_saved;
                }
            }
            //std::cout << "stereo " << disp_seq << " latency:" << std::chrono::duration<float, std::milli>(std::chrono::steady_clock::now() - disp_data->getTimestamp()).count() << " ms\n";
        } else if (q_name == "imu") {
            auto imuData = imuQueue->get<dai::IMUData>();
            auto imuPackets = imuData->packets;
            for(const auto& imuPacket : imuPackets) {
                auto& acc = imuPacket.acceleroMeter;
                auto& gyro = imuPacket.gyroscope;
                //std::cout << "imu latency, acc:" << std::chrono::duration<float, std::milli>(std::chrono::steady_clock::now() - acc.getTimestamp()).count() << " ms, gyro:" << std::chrono::duration<float, std::milli>(std::chrono::steady_clock::now() - gyro.getTimestamp()).count() << " ms\n";
                big_buf[0] = std::chrono::duration<double>(gyro.getTimestampDevice().time_since_epoch()).count();
                // translate to ros frame, easier to understand in rviz
                if (dev_type == OAK_D) {
                    big_buf[1] = acc.z;
                    big_buf[2] = acc.y;
                    big_buf[3] = -acc.x;
                    big_buf[4] = gyro.z;
                    big_buf[5] = gyro.y;
                    big_buf[6] = -gyro.x;
                } else {
                    big_buf[1] = -acc.z;
                    big_buf[2] = -acc.y;
                    big_buf[3] = -acc.x;
                    big_buf[4] = -gyro.z;
                    big_buf[5] = -gyro.y;
                    big_buf[6] = -gyro.x;
                }
                sendto(ipc_sock, big_buf, 7*sizeof(double), 0, (struct sockaddr*)&imu_addr, sizeof(struct sockaddr_un));
                feat_tee(FEAT_SID_IMU, big_buf, (uint32_t)(7*sizeof(double)));  // #78
            }
            if (!imu_ok) {
                imu_ok = true;
                std::cout<< "imu ok\n";
            }
        } else if (q_name == "still") {  // #72: a triggered RGB still arrived
            auto still = still_queue->get<dai::ImgFrame>();
            auto wall = std::chrono::system_clock::now();
            std::string stem = make_stem(node, still_saved, wall);
            std::string base = session_dir + "/" + stem;
            std::vector<int> jp = {cv::IMWRITE_JPEG_QUALITY, jpeg_q};
            // NV12 -> BGR by hand: depthai-core here is built WITHOUT its OpenCV support,
            // so ImgFrame::getCvFrame() is unavailable. The ColorCamera still ISP output is
            // NV12 (Y plane h rows + interleaved UV h/2 rows).
            int sw = still->getWidth(), sh = still->getHeight();
            cv::Mat nv12(sh * 3 / 2, sw, CV_8UC1, still->getData().data());
            cv::Mat bgr;
            cv::cvtColor(nv12, bgr, cv::COLOR_YUV2BGR_NV12);
            capture::Job job;  // #89: encode on the hot path, persist durably off it
            if (cv::imencode(".jpg", bgr, job.bytes, jp)) {
                long long exp_us = std::chrono::duration_cast<std::chrono::microseconds>(
                                       still->getExposureTime()).count();
                job.path = base + ".jpg";
                job.sidecar_path = base + ".json";
                job.sidecar = build_sidecar(node, still_saved, stem + ".jpg", "still", wall,
                                            ts_ns(still->getTimestampDevice()), still->getSequenceNum(),
                                            still->getWidth(), still->getHeight(),
                                            exp_us > 0 ? exp_us : -1, still->getSensitivity());
                if (writer->submit(std::move(job))) ++still_saved;
            }
        }

        // #72: trigger the next still on cadence (device returns it on the "still" queue).
        if (capture && still_hz > 0 && due(last_still_trig, still_hz)) {
            dai::CameraControl ctrl;
            ctrl.setCaptureStill(true);
            still_ctrl_queue->send(ctrl);
        }

        if (l_seq == r_seq && r_seq == disp_seq) {
            //auto t1 = std::chrono::steady_clock::now();
            l_seq = -1;
            r_seq = -2;
            disp_seq = -3;
            std::map<int , MyPoint2d> features;
            int c = 0;
            big_buf[1] = std::chrono::duration<double>(features_tp.time_since_epoch()).count();
            double* buf_ptr = big_buf + 2;
            for (const auto &l_feature : l_features) {
                float x = l_feature.position.x;
                float y = l_feature.position.y;
                double cur_un_x = l_inv_k11 * x + l_inv_k13;
                double cur_un_y = l_inv_k22 * y + l_inv_k23;
                features[l_feature.id] = MyPoint2d(cur_un_x, cur_un_y);
                auto lr_id = lr_id_mapping.find(l_feature.id);
                if (lr_id != lr_id_mapping.end()) {
                    auto r_feature = r_cur_features.find(lr_id->second);
                    if (r_feature != r_cur_features.end()) {
                        double dt = std::chrono::duration<double>(features_tp - prv_features_tp).count();
                        double vx = 0, vy = 0;
                        auto prv_pos = l_prv_features.find(l_feature.id);
                        if (prv_pos != l_prv_features.end()) {
                            vx = (cur_un_x - prv_pos->second.x) / dt;
                            vy = (cur_un_y - prv_pos->second.y) / dt;
                        }
                        buf_ptr[0] = l_feature.id;
                        buf_ptr[1] = cur_un_x;
                        buf_ptr[2] = cur_un_y;
                        buf_ptr[3] = x;
                        buf_ptr[4] = y;
                        buf_ptr[5] = vx;
                        buf_ptr[6] = vy;

                        x = r_feature->second.x;
                        y = r_feature->second.y;
                        vx = 0;
                        vy = 0;
                        cur_un_x = r_inv_k11 * x + r_inv_k13;
                        cur_un_y = r_inv_k22 * y + r_inv_k23;
                        prv_pos = r_prv_features.find(r_feature->first);
                        if (prv_pos != r_prv_features.end()) {
                            vx = (cur_un_x - prv_pos->second.x) / dt;
                            vy = (cur_un_y - prv_pos->second.y) / dt;
                        }
                        buf_ptr[7] = cur_un_x;
                        buf_ptr[8] = cur_un_y;
                        buf_ptr[9] = x;
                        buf_ptr[10] = y;
                        buf_ptr[11] = vx;
                        buf_ptr[12] = vy;

                        if (c < 118) {
                            ++c;
                            buf_ptr += 13;
                        }

                        continue;
                    }
                }
                float row = roundf(y);
                float col = roundf(x);
                if (row > CAM_H - 1) row = CAM_H - 1;
                if (col > CAM_W - 1) col = CAM_W - 1;
                int disp = disp_frame[row * CAM_W + col];
                if (disp > 0) {
                    for (const auto &r_feature : r_features) {
                        float dy = y - r_feature.position.y;
                        float dx = x - disp - r_feature.position.x;
                        if (dy * dy + dx * dx <= PAIR_DIST_SQ) { //pair found
                            lr_id_mapping[l_feature.id] = r_feature.id;
                            double dt = std::chrono::duration<double>(features_tp - prv_features_tp).count();
                            double vx = 0, vy = 0;
                            auto prv_pos = l_prv_features.find(l_feature.id);
                            if (prv_pos != l_prv_features.end()) {
                                vx = (cur_un_x - prv_pos->second.x) / dt;
                                vy = (cur_un_y - prv_pos->second.y) / dt;
                            }
                            buf_ptr[0] = l_feature.id;
                            buf_ptr[1] = cur_un_x;
                            buf_ptr[2] = cur_un_y;
                            buf_ptr[3] = x;
                            buf_ptr[4] = y;
                            buf_ptr[5] = vx;
                            buf_ptr[6] = vy;

                            x = r_feature.position.x;
                            y = r_feature.position.y;
                            vx = 0;
                            vy = 0;
                            cur_un_x = r_inv_k11 * x + r_inv_k13;
                            cur_un_y = r_inv_k22 * y + r_inv_k23;
                            prv_pos = r_prv_features.find(r_feature.id);
                            if (prv_pos != r_prv_features.end()) {
                                vx = (cur_un_x - prv_pos->second.x) / dt;
                                vy = (cur_un_y - prv_pos->second.y) / dt;
                            }
                            buf_ptr[7] = cur_un_x;
                            buf_ptr[8] = cur_un_y;
                            buf_ptr[9] = x;
                            buf_ptr[10] = y;
                            buf_ptr[11] = vx;
                            buf_ptr[12] = vy;

                            if (c < 118) {
                                ++c;
                                buf_ptr += 13;
                            }

                            break;
                        }
                    }
                }
            }
            ccc++;
            if (ccc > 60) {
                ccc = 0;
                std::cout << c << " features\n";
            }
            if (imu_ok && c > 0) {
                big_buf[0] = c;
                sendto(ipc_sock, big_buf, 13*sizeof(double)*c+2*sizeof(double), 0, (struct sockaddr*)&features_addr, sizeof(struct sockaddr_un));
                feat_tee(FEAT_SID_FEATURES, big_buf, (uint32_t)(13*sizeof(double)*c+2*sizeof(double)));  // #78
            }
            l_prv_features = features;
            prv_features_tp = features_tp;
            r_prv_features.clear();
            for (const auto &r_feature : r_features) {
                r_prv_features[r_feature.id] = MyPoint2d(r_inv_k11 * r_feature.position.x + r_inv_k13, r_inv_k22 * r_feature.position.y + r_inv_k23);
            }
            //auto t2 = std::chrono::steady_clock::now();
            //std::cout << pp_msg.points.size() << " points, " << std::chrono::duration<float, std::milli>(t2-t1).count() << " ms\n";
        }
    }

    close(ipc_sock);
    if (feat_file) {  // #78/#89: final durable flush, then close the input tee
        fflush(feat_file);
        fdatasync(fileno(feat_file));
        fclose(feat_file);
    }
    if (writer) {  // #89: drain queued image writes to disk before exiting
        writer->stop();
        std::cout << "capture: writer drained (" << writer->dropped()
                  << " images dropped under backpressure)\n";
    }
    printf("bye\n");

    return 0;
}
