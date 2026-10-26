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
        capture::Session s({"bench", "boot", dir, {"color", "left"}, 2, flag, status, "test"});
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
    for (const auto& l : led) kept += l["kept"].get<bool>();
    CHECK(kept == 2);
    CHECK(led[0]["exposure_us"] == 1000);

    // No gate file at all means paused, and no gate path means always capturing.
    {
        fs::remove(flag);
        capture::Session s({"bench", "boot2", dir, {"color"}, 0, flag, std::nullopt, "test"});
        s.begin("MX");
        s.tick(0);
        CHECK(!s.capturing());
    }
    {
        capture::Session s({"bench", "boot3", dir, {"color"}, 0, std::nullopt, std::nullopt, "test"});
        s.begin("MX");
        s.tick(0);
        CHECK(s.capturing());
    }

    fs::remove_all(dir);
    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "test_session: all checks passed\n";
    return 0;
}
