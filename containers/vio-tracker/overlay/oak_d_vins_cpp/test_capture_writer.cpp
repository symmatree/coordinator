// Standalone unit test for capture_writer.hpp (coordinator #89). No depthai/OpenCV --
// exercises the durable-write + bounded-queue logic that the tracker can't compile-test
// on this host. Build + run (one line):
//   g++ -std=c++11 -pthread -o /tmp/tcw containers/vio-tracker/overlay/oak_d_vins_cpp/test_capture_writer.cpp && /tmp/tcw
#include "capture_writer.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include <dirent.h>
#include <sys/stat.h>

static int failures = 0;
#define CHECK(cond, msg)                                                    \
    do {                                                                    \
        if (!(cond)) { std::cerr << "FAIL: " << (msg) << "\n"; ++failures; } \
    } while (0)

static std::string read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static bool exists(const std::string& path) {
    struct stat st;
    return ::stat(path.c_str(), &st) == 0;
}

// Count files in dir whose name ends in `.tmp` -- must always be zero after writes.
static int count_tmp(const std::string& dir) {
    int n = 0;
    DIR* d = ::opendir(dir.c_str());
    if (!d) return -1;
    for (struct dirent* e = ::readdir(d); e; e = ::readdir(d)) {
        std::string name(e->d_name);
        if (name.size() >= 4 && name.substr(name.size() - 4) == ".tmp") ++n;
    }
    ::closedir(d);
    return n;
}

int main() {
    char tmpl[] = "/tmp/tcw_XXXXXX";
    std::string dir = ::mkdtemp(tmpl);

    // 1. durable_write leaves the full content and no .tmp behind.
    {
        std::string path = dir + "/one.bin";
        std::string payload = "hello-\x00-world";  // embedded NUL
        payload.push_back('!');
        bool ok = capture::durable_write(path, payload.data(), payload.size());
        CHECK(ok, "durable_write returns true");
        CHECK(exists(path), "durable_write created the final file");
        CHECK(read_file(path) == payload, "durable_write content matches (incl. NUL)");
        CHECK(count_tmp(dir) == 0, "durable_write left no .tmp file");
    }

    // 2. Writer round-trip (timing-independent): with a queue deep enough to hold the
    //    whole batch, nothing drops and stop() drains all -- every job lands, image +
    //    sidecar, content intact. (Deep queue avoids any dependence on fsync speed.)
    {
        const int K = 20;
        {
            capture::Writer w(dir, K + 1);
            for (int i = 0; i < K; ++i) {
                capture::Job j;
                j.path = dir + "/img_" + std::to_string(i) + ".jpg";
                std::string b = "IMG" + std::to_string(i);
                j.bytes.assign(b.begin(), b.end());
                j.sidecar_path = dir + "/img_" + std::to_string(i) + ".json";
                j.sidecar = "{\"seq\":" + std::to_string(i) + "}";
                CHECK(w.submit(std::move(j)), "submit within capacity is accepted");
            }
            w.stop();  // drains everything queued
            CHECK(w.dropped() == 0, "no drops when the queue is deep enough");
        }
        for (int i = 0; i < K; ++i) {
            std::string ip = dir + "/img_" + std::to_string(i) + ".jpg";
            std::string sp = dir + "/img_" + std::to_string(i) + ".json";
            CHECK(exists(ip), "image written");
            CHECK(read_file(ip) == "IMG" + std::to_string(i), "image content matches");
            CHECK(exists(sp), "sidecar written");
        }
        CHECK(count_tmp(dir) == 0, "no .tmp files after drain");
    }

    // 3. Backpressure: a fast burst may drop, but accounting stays exact --
    //    (files written) + (dropped) == (submitted), and nothing is torn.
    {
        std::string bdir = dir + "/burst";
        ::mkdir(bdir.c_str(), 0775);
        const int K = 200;
        uint64_t dropped = 0;
        int submitted_ok = 0;
        {
            capture::Writer w(bdir, 4);
            for (int i = 0; i < K; ++i) {
                capture::Job j;
                j.path = bdir + "/b_" + std::to_string(i) + ".jpg";
                std::string b = "B" + std::to_string(i);
                j.bytes.assign(b.begin(), b.end());
                if (w.submit(std::move(j))) ++submitted_ok;  // no sleep: race the worker
            }
            w.stop();
            dropped = w.dropped();
        }
        int written = 0;
        DIR* d = ::opendir(bdir.c_str());
        for (struct dirent* e = ::readdir(d); e; e = ::readdir(d)) {
            std::string name(e->d_name);
            if (name.size() >= 4 && name.substr(name.size() - 4) == ".jpg") ++written;
        }
        ::closedir(d);
        CHECK(submitted_ok + (int)dropped == K, "accepted + dropped == submitted");
        CHECK(written == submitted_ok, "every accepted job produced exactly one file");
        CHECK(count_tmp(bdir) == 0, "no torn .tmp after a dropping burst");
        std::cerr << "  (burst: " << written << " written, " << dropped << " dropped)\n";
    }

    if (failures == 0) std::cout << "capture_writer: all checks passed\n";
    else std::cout << "capture_writer: " << failures << " FAILED\n";
    return failures == 0 ? 0 : 1;
}
