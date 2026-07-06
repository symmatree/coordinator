// main_offline.cpp -- deterministic offline VINS harness (coordinator #35 / #42).
//
// The stock main.cpp is an online, real-time reader: it poll()s AF_UNIX sockets and
// couples the estimate to arrival timing, and the estimator's Ceres solve is bounded by
// a wall-clock budget (max_solver_time). That makes replay neither reproducible nor
// un-starved (see docs/vio-offline-replay.md, analysis/vio-quality-experiments.md T1/T7).
//
// This binary instead reads a vio-ipc-record fixture *file* directly, feeds IMU + feature
// frames to the real estimator in t_mono order synchronously, and writes the resulting
// pose (keyed to the sensor timestamp) as CSV to stdout. Determinism comes from two knobs
// forced here, not from config: MULTIPLE_THREAD=0 (inputFeature processes synchronously)
// and SOLVER_TIME huge (Ceres runs the full iteration count instead of a wall-clock
// budget). The estimator math is untouched -- only the I/O shell changes.
//
//   vins_fusion_offline <config.yaml> <fixture.feat>  > pose.csv

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <utility>
#include <vector>
#include <netinet/in.h>

#include "estimator/estimator.h"
#include "estimator/parameters.h"
#include "utility/visualization.h"

// Defined here because visualization.cpp declares them extern (the opt-in UDP debug feed).
// Unused offline: pub_addr stays zeroed, so pubOdometry's AF_INET branch is skipped, and
// its chobits_server send goes nowhere harmlessly.
int pub_sock = 0;
struct sockaddr_in pub_addr;

// The estimator lib references this "keep running" flag (defined in the stock main.cpp).
// Offline we run to completion regardless; just satisfy the link with it set true.
bool gogogo = true;

// vio-ipc-record frame header: little-endian <ddHI> = t_mono, t_unix, socket_id, length
// (packed, 22 bytes), then <length> payload bytes.
namespace {
constexpr size_t HDR = 22;
struct Frame {
    double t_mono;
    uint16_t sid;
    uint32_t len;
    size_t off;  // payload offset into the file buffer
};
}  // namespace

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s <config.yaml> <fixture.feat> <out.csv>\n", argv[0]);
        return 1;
    }
    Estimator estimator;
    readParameters(argv[1]);

    // Determinism: force these BEFORE setParameter -- with MULTIPLE_THREAD from the config
    // (=1), setParameter would spawn the async processing thread, racing our synchronous
    // feed (non-deterministic) and aborting on exit (unjoined). SOLVER_TIME huge makes
    // Ceres run the full iteration count instead of a wall-clock budget.
    MULTIPLE_THREAD = 0;
    SOLVER_TIME = 1e9;

    estimator.setParameter();
    registerPub();  // inits the chobits_server sender pubOdometry uses (harmless offline)

    // Pose CSV goes to a file so vins's own stdout diagnostics don't pollute it.
    FILE *out = fopen(argv[3], "w");
    if (!out) {
        fprintf(stderr, "cannot open output %s\n", argv[3]);
        return 1;
    }

    FILE *f = fopen(argv[2], "rb");
    if (!f) {
        fprintf(stderr, "cannot open fixture %s\n", argv[2]);
        return 1;
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    std::vector<uint8_t> data(n > 0 ? n : 0);
    if (n <= 0 || fread(data.data(), 1, n, f) != static_cast<size_t>(n)) {
        fprintf(stderr, "cannot read fixture %s\n", argv[2]);
        fclose(f);
        return 1;
    }
    fclose(f);

    std::vector<Frame> frames;
    size_t off = 0;
    while (off + HDR <= static_cast<size_t>(n)) {
        Frame fr;
        memcpy(&fr.t_mono, &data[off], 8);
        memcpy(&fr.sid, &data[off + 16], 2);
        memcpy(&fr.len, &data[off + 18], 4);
        off += HDR;
        if (off + fr.len > static_cast<size_t>(n)) break;  // truncated tail
        fr.off = off;
        off += fr.len;
        frames.push_back(fr);
    }
    // Feed in true send (t_mono) order -- the order the live estimator received them.
    std::stable_sort(frames.begin(), frames.end(),
                     [](const Frame &a, const Frame &b) { return a.t_mono < b.t_mono; });

    fprintf(out, "t,qw,qx,qy,qz,px,py,pz,vx,vy,vz\n");

    // Copy each payload into an 8-byte-aligned buffer before reading doubles (frame
    // offsets are unaligned; misaligned double reads are UB on arm64).
    std::vector<double> vals;
    Eigen::Matrix<double, 7, 1> xyz_uv_velocity;
    for (const Frame &fr : frames) {
        vals.resize(fr.len / sizeof(double));
        memcpy(vals.data(), &data[fr.off], vals.size() * sizeof(double));
        const double *d = vals.data();

        if (fr.sid == 0) {  // IMU: t_dev, ax,ay,az, gx,gy,gz
            if (vals.size() < 7) continue;
            estimator.inputIMU(d[0], Eigen::Vector3d(d[1], d[2], d[3]),
                               Eigen::Vector3d(d[4], d[5], d[6]));
        } else if (fr.sid == 1) {  // features: num, t, then num x 13 doubles (stereo)
            if (vals.size() < 2) continue;
            int num = static_cast<int>(d[0]);
            double t = d[1];
            if (vals.size() < static_cast<size_t>(2 + num * 13)) continue;
            const double *fd = d + 2;
            std::map<int, std::vector<std::pair<int, Eigen::Matrix<double, 7, 1>>>> featureFrame;
            for (int i = 0; i < num; ++i) {
                int id = static_cast<int>(fd[0]);
                xyz_uv_velocity << fd[1], fd[2], 1, fd[3], fd[4], fd[5], fd[6];
                featureFrame[id].emplace_back(0, xyz_uv_velocity);
                xyz_uv_velocity << fd[7], fd[8], 1, fd[9], fd[10], fd[11], fd[12];
                featureFrame[id].emplace_back(1, xyz_uv_velocity);
                fd += 13;
            }
            estimator.inputFeature(t, featureFrame);  // synchronous (MULTIPLE_THREAD=0)

            if (estimator.solver_flag == Estimator::SolverFlag::NON_LINEAR) {
                Eigen::Quaterniond q(estimator.Rs[WINDOW_SIZE]);
                const Eigen::Vector3d &P = estimator.Ps[WINDOW_SIZE];
                const Eigen::Vector3d &V = estimator.Vs[WINDOW_SIZE];
                fprintf(out, "%.6f,%.7f,%.7f,%.7f,%.7f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f\n",
                        estimator.Headers[WINDOW_SIZE], q.w(), q.x(), q.y(), q.z(),
                        P.x(), P.y(), P.z(), V.x(), V.y(), V.z());
            }
        }
    }
    fclose(out);
    return 0;
}
