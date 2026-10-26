// The control loop without a camera: warmup, the gate, the ledger, what is written and
// what the status says. Run by the image build.
#include <unistd.h>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "session.hpp"

namespace fs = std::filesystem;
using json = nlohmann::json;

static int failures = 0;

#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAILED " #cond << "\n"; \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

static std::string slurp(const fs::path& p) {
    std::ifstream f(p, std::ios::binary);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static void spit(const fs::path& p, const std::string& s) { std::ofstream(p, std::ios::binary) << s; }

static std::vector<json> lines(const fs::path& p) {
    std::vector<json> out;
    std::istringstream in(slurp(p));
    for (std::string l; std::getline(in, l);) out.push_back(json::parse(l));
    return out;
}

static size_t count_ext(const fs::path& dir, const std::string& ext) {
    size_t n = 0;
    for (const auto& e : fs::directory_iterator(dir)) n += e.path().extension() == ext;
    return n;
}

static capture::SessionConfig cfg(std::string session, fs::path dir, std::vector<std::string> streams,
                                  int warmup, std::optional<fs::path> flag, std::optional<fs::path> status) {
    return {.node = "bench",
            .session = std::move(session),
            .dir = std::move(dir),
            .streams = std::move(streams),
            .warmup_frames = warmup,
            .flag_path = std::move(flag),
            .status_path = std::move(status),
            .log_name = "test"};
}

int main() {
    const fs::path dir = fs::temp_directory_path() / ("capture-session-test-" + std::to_string(::getpid()));
    fs::create_directories(dir);
    const fs::path flag = dir / "fc_armed";
    const fs::path status = dir / "status";

    int encodes = 0;
    auto encode = [&] {
        ++encodes;
        return std::vector<uint8_t>{1, 2, 3};
    };
    auto frame = [](const char* kind, int64_t seq) {
        capture::Frame f;
        f.kind = kind;
        f.device_seq = seq;
        f.monotonic_ns = 1'000'000'000 + seq;
        f.width = 4;
        f.height = 2;
        f.fields = {{"exposure_us", 1000}};
        return f;
    };

    {
        capture::Session s(cfg("boot", dir, {"color", "left"}, 2, flag, status));
        s.set_status("camera", "present");
        s.begin("MX");
        CHECK(json::parse(slurp(status))["phase"] == "warmup");
        CHECK(json::parse(slurp(status))["camera"] == "present");

        // Warmup needs every stream: two colour frames are not enough on their own.
        spit(flag, "1\n");
        s.frame(frame("color", 0), ".jpg", encode);
        s.frame(frame("color", 1), ".jpg", encode);
        s.tick(10.0);
        CHECK(!s.capturing());
        s.frame(frame("left", 0), ".pgm", encode);
        s.frame(frame("left", 1), ".pgm", encode);
        s.tick(10.2);
        CHECK(s.capturing());
        auto st = json::parse(slurp(status));
        CHECK(st["warm"] == true);
        CHECK(st["ready"] == true);
        CHECK(st["capturing"] == true);
        CHECK(encodes == 0);  // nothing kept during warmup

        // Armed: kept, encoded, named by prefix/kind/seq, sidecar carries the fields.
        s.frame(frame("color", 2), ".jpg", encode);
        s.frame(frame("left", 3), ".pgm", encode);  // skips device_seq 2: one gap
        CHECK(encodes == 2);
        CHECK(s.counts("color").kept == 1);
        CHECK(s.counts("left").device_seq_gaps == 1);

        // Disarmed: the gate is read at most every 100 ms, then frames stop being kept.
        spit(flag, "0\n");
        s.tick(10.25);
        CHECK(s.capturing());
        s.tick(10.4);
        CHECK(!s.capturing());
        s.frame(frame("color", 3), ".jpg", encode);
        CHECK(encodes == 2);
        CHECK(s.counts("color").seen == 4);
        s.stop();
        CHECK(json::parse(slurp(status))["phase"] == "stopped");
        CHECK(json::parse(slurp(status))["ready"] == false);
    }

    CHECK(count_ext(dir, ".jpg") == 1);
    CHECK(count_ext(dir, ".pgm") == 1);
    fs::path jpg;
    for (const auto& e : fs::directory_iterator(dir))
        if (e.path().extension() == ".jpg") jpg = e.path();
    CHECK(jpg.filename().string().rfind("MX_color_00000000_", 0) == 0);
    auto side = json::parse(slurp(fs::path(jpg).replace_extension(".json")));
    CHECK(side["file"] == jpg.filename().string());
    CHECK(side["kind"] == "color");
    CHECK(side["device_seq"] == 2);
    CHECK(side["exposure_us"] == 1000);
    CHECK(side["clk"].contains("boot_ns"));
    CHECK(slurp(jpg) == std::string("\x01\x02\x03", 3));

    // The ledger has every frame, kept or not.
    auto led = lines(dir / "MX_boot.frames.jsonl");
    CHECK(led.size() == 7);
    int kept = 0;
    for (const auto& l : led) kept += l["decision"] == "kept";
    CHECK(kept == 2);
    CHECK(led[0]["decision"] == "warmup");
    CHECK(led[6]["decision"] == "paused");
    CHECK(led[0]["exposure_us"] == 1000);
    CHECK(led[0]["clk"].contains("boot_ns"));

    // A synced session: warm is not ready, nothing is kept until sync, and one stream's
    // names carry no kind -- the campods' layout.
    {
        const fs::path sdir = dir / "synced";
        fs::create_directories(sdir);
        spit(flag, "1\n");
        auto c = cfg("boot5", sdir, {"still"}, 1, flag, status);
        c.node = "campod-se";
        c.sync = true;
        c.ledger_file = "frames.jsonl";
        capture::Session s(std::move(c));
        s.begin("campod-se");
        CHECK(json::parse(slurp(status))["sync_ready"].is_null());
        s.frame(frame("still", 0), ".jpg", encode);
        s.tick(20.0);
        auto st = json::parse(slurp(status));
        CHECK(st["warm"] == true);
        CHECK(st["ready"] == false);
        CHECK(!s.capturing());
        s.set_sync_ready(false);
        CHECK(json::parse(slurp(status))["sync_ready"] == false);
        s.frame(frame("still", 1), ".jpg", encode);
        s.set_sync_ready(true);
        st = json::parse(slurp(status));
        CHECK(st["ready"] == true);
        CHECK(st["capturing"] == true);
        s.frame(frame("still", 2), ".jpg", encode);
        s.stop();
        auto sled = lines(sdir / "frames.jsonl");
        CHECK(sled.size() == 3);
        CHECK(sled[1]["decision"] == "sync_wait");
        CHECK(sled[2]["decision"] == "kept");
        CHECK(sled[2]["kept_seq"] == 0);
        CHECK(count_ext(sdir, ".jpg") == 1);
        for (const auto& e : fs::directory_iterator(sdir))
            if (e.path().extension() == ".jpg") CHECK(e.path().filename().string().rfind("campod-se_00000000_", 0) == 0);
    }

    // An encode that throws costs that frame, not the session.
    {
        const fs::path edir = dir / "encode";
        fs::create_directories(edir);
        capture::Session s(cfg("boot6", edir, {"still"}, 0, std::nullopt, std::nullopt));
        s.begin("MX");
        s.tick(0);
        s.frame(frame("still", 0), ".jpg", []() -> std::vector<uint8_t> { throw std::runtime_error("no"); });
        s.frame(frame("still", 1), ".jpg", encode);
        s.stop();
        auto eled = lines(edir / "MX_boot6.frames.jsonl");
        CHECK(eled[0]["decision"] == "encode_failed");
        CHECK(eled[1]["decision"] == "kept");
        CHECK(s.counts("still").kept == 1);
    }

    // No gate file at all means paused, and no gate path means always capturing.
    {
        fs::remove(flag);
        capture::Session s(cfg("boot2", dir, {"color"}, 0, flag, std::nullopt));
        s.begin("MX");
        s.tick(0);
        CHECK(!s.capturing());
    }
    {
        capture::Session s(cfg("boot3", dir, {"color"}, 0, std::nullopt, std::nullopt));
        s.begin("MX");
        s.tick(0);
        CHECK(s.capturing());
    }

    // A failure is published as such, with its reason, and a later stop() does not hide it.
    {
        capture::Session s(cfg("boot4", dir, {"color"}, 0, std::nullopt, status));
        s.set_status("camera", "present");
        s.publish();
        s.fail("Desired FPS 1 is lower than min FPS");
        s.stop();
        auto st = json::parse(slurp(status));
        CHECK(st["phase"] == "failed");
        CHECK(st["error"] == "Desired FPS 1 is lower than min FPS");
        CHECK(st["ready"] == false);
    }

    fs::remove_all(dir);
    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "test_session: all checks passed\n";
    return 0;
}
