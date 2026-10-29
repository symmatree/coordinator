// Hardware-free checks of the conventions, run by the image build: a failure fails the
// build, the same as the campod image's build-time tests.
#include <unistd.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

#include "conventions.hpp"
#include "durable_writer.hpp"

namespace fs = std::filesystem;

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

int main() {
    const fs::path dir = fs::temp_directory_path() / ("oak-capture-test-" + std::to_string(::getpid()));
    fs::create_directories(dir);

    // Time formatting: microseconds, UTC, and floor for the fraction.
    const int64_t t = 1'793'046'751'123'456'789;  // 2026-10-26T20:32:31.123456789Z
    CHECK(capture::utc_iso(t) == "2026-10-26T20:32:31.123456Z");
    CHECK(capture::utc_stem(t) == "20261026T203231_123456Z");
    CHECK(capture::utc_iso(-1) == "1969-12-31T23:59:59.999999Z");

    // mono -> wall by the triple's offset.
    capture::ClockTriple c{5, 1000, 9000};
    CHECK(capture::mono_to_wall_ns(1500, c) == 9500);
    const auto live = capture::clock_triple();
    CHECK(live.wall_ns > live.mono_ns);
    CHECK(capture::to_json(c)["wall_ns"] == 9000);

    // The gate: no path means always; absent means paused; the four true spellings.
    CHECK(capture::capture_wanted(std::nullopt));
    CHECK(!capture::capture_wanted(dir / "absent"));
    for (const char* v : {"1\n", "true", " on ", "yes\n"}) {
        spit(dir / "flag", v);
        CHECK(capture::capture_wanted(dir / "flag"));
    }
    for (const char* v : {"0\n", "", "armed", "no"}) {
        spit(dir / "flag", v);
        CHECK(!capture::capture_wanted(dir / "flag"));
    }

    // An empty environment value means "none".
    ::setenv("OAK_TEST_PATH", "  ", 1);
    CHECK(!capture::env_path("OAK_TEST_PATH", "/x"));
    ::setenv("OAK_TEST_PATH", "/ipc/fc_armed", 1);
    CHECK(capture::env_path("OAK_TEST_PATH", "/x") == fs::path("/ipc/fc_armed"));
    ::unsetenv("OAK_TEST_PATH");
    CHECK(capture::env_path("OAK_TEST_PATH", "/x") == fs::path("/x"));

    // Status: a whole JSON document, no temp file left behind; a bad path is swallowed.
    capture::write_status(dir / "status", {{"ready", true}});
    CHECK(nlohmann::json::parse(slurp(dir / "status"))["ready"] == true);
    CHECK(!fs::exists(dir / "status.tmp"));
    capture::write_status(dir / "no-such-dir" / "status", {{"ready", true}});

    // Node name: the override, then the host's file, trimmed.
    spit(dir / "hostname", "coordinator\n");
    ::setenv("OAK_TEST_NODE", "bench", 1);
    CHECK(capture::node_name("OAK_TEST_NODE", dir / "hostname") == "bench");
    ::unsetenv("OAK_TEST_NODE");
    CHECK(capture::node_name("OAK_TEST_NODE", dir / "hostname") == "coordinator");

    // Manifests: named by FLEET_UNIT or as given; a missing source is skipped.
    spit(dir / "container-image", "ORG_X=\"y\"\nFLEET_UNIT=\"oak-capture\"\n");
    spit(dir / "fleet-image", "IMAGE=1\n");
    capture::copy_manifests(dir / "session", {{dir / "container-image", ""},
                                              {dir / "fleet-image", "fleet-image"},
                                              {dir / "absent", "absent"}});
    CHECK(slurp(dir / "session" / "manifests" / "oak-capture") == slurp(dir / "container-image"));
    CHECK(fs::exists(dir / "session" / "manifests" / "fleet-image"));
    CHECK(!fs::exists(dir / "session" / "manifests" / "absent"));

    // Two writers of the same manifests at once. On a campod the camera and the accel
    // reader are each pid 2 in their own containers, so a pid-named temp was one shared
    // file and one rename threw (campod-nw 2026-10-29). Two threads share a pid too.
    {
        bool threw = false;
        auto race = [&] {
            try {
                for (int i = 0; i < 200; ++i)
                    capture::copy_manifests(dir / "race", {{dir / "fleet-image", "fleet-image"}});
            } catch (...) { threw = true; }
        };
        std::thread a(race), b(race);
        a.join(); b.join();
        CHECK(!threw);
        CHECK(slurp(dir / "race" / "manifests" / "fleet-image") == "IMAGE=1\n");
        int leftovers = 0;
        for (const auto& e : fs::directory_iterator(dir / "race" / "manifests"))
            if (e.path().filename().string().front() == '.') ++leftovers;
        CHECK(leftovers == 0);
    }

    // PGM: header, then each row's first `width` bytes, skipping the stride padding.
    const uint8_t img[] = {1, 2, 3, 99, 4, 5, 6, 99};
    const auto pgm = capture::encode_pgm(img, 3, 2, 4);
    const std::string expect = std::string("P5\n3 2\n255\n") + std::string("\x01\x02\x03\x04\x05\x06", 6);
    CHECK(std::string(pgm.begin(), pgm.end()) == expect);

    // The writer: image and sidecar land complete, and stop() drains the queue.
    {
        capture::Writer w(dir.string());
        capture::Job job;
        job.path = (dir / "a.jpg").string();
        job.bytes = {0xff, 0xd8};
        job.sidecar_path = (dir / "a.json").string();
        job.sidecar = "{}";
        CHECK(w.submit(std::move(job)));
        w.stop();
        CHECK(w.dropped() == 0);
    }
    CHECK(fs::file_size(dir / "a.jpg") == 2);
    CHECK(slurp(dir / "a.json") == "{}");
    CHECK(!fs::exists(dir / "a.jpg.tmp"));

    fs::remove_all(dir);
    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "test_conventions: all checks passed\n";
    return 0;
}
