#include "conventions.hpp"

#include <sys/stat.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <regex>
#include <sstream>

namespace capture {

namespace fs = std::filesystem;

namespace {

int64_t read_clock(clockid_t id) {
    timespec ts{};
    clock_gettime(id, &ts);
    return int64_t{ts.tv_sec} * 1'000'000'000 + ts.tv_nsec;
}

std::string trim(const std::string& s) {
    const auto b = s.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) return {};
    const auto e = s.find_last_not_of(" \t\r\n");
    return s.substr(b, e - b + 1);
}

std::optional<std::string> read_file(const fs::path& p) {
    std::ifstream f(p, std::ios::binary);
    if (!f) return std::nullopt;
    std::ostringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

// Floor division, so an instant before the epoch still lands in the right second.
std::pair<std::time_t, int64_t> split_us(int64_t wall_ns) {
    int64_t us = wall_ns / 1000;
    if (wall_ns % 1000 < 0) --us;
    int64_t sec = us / 1'000'000;
    int64_t frac = us % 1'000'000;
    if (frac < 0) {
        frac += 1'000'000;
        --sec;
    }
    return {static_cast<std::time_t>(sec), frac};
}

std::string format_utc(int64_t wall_ns, const char* date_fmt, const char* frac_fmt) {
    const auto [sec, frac] = split_us(wall_ns);
    std::tm tv{};
    gmtime_r(&sec, &tv);
    char date[32];
    std::strftime(date, sizeof(date), date_fmt, &tv);
    char out[64];
    std::snprintf(out, sizeof(out), frac_fmt, date, static_cast<long long>(frac));
    return out;
}

}  // namespace

ClockTriple clock_triple() {
    return {read_clock(CLOCK_BOOTTIME), read_clock(CLOCK_MONOTONIC), read_clock(CLOCK_REALTIME)};
}

nlohmann::json to_json(const ClockTriple& c) {
    return {{"boot_ns", c.boot_ns}, {"mono_ns", c.mono_ns}, {"wall_ns", c.wall_ns}};
}

std::string utc_iso(int64_t wall_ns) {
    return format_utc(wall_ns, "%Y-%m-%dT%H:%M:%S", "%s.%06lldZ");
}

std::string utc_stem(int64_t wall_ns) {
    return format_utc(wall_ns, "%Y%m%dT%H%M%S", "%s_%06lldZ");
}

std::string node_name(const char* override_env, const fs::path& host_hostname) {
    if (const char* o = std::getenv(override_env); o && *o) return o;
    if (auto body = read_file(host_hostname)) {
        if (auto name = trim(*body); !name.empty()) return name;
    }
    char buf[256] = {};
    gethostname(buf, sizeof(buf) - 1);
    return buf;
}

std::string boot_id() {
    auto body = read_file("/proc/sys/kernel/random/boot_id");
    if (!body) throw std::runtime_error("cannot read /proc/sys/kernel/random/boot_id");
    return trim(*body);
}

void copy_manifests(const fs::path& session_dir,
                    const std::vector<std::pair<fs::path, std::string>>& sources) {
    const fs::path out = session_dir / "manifests";
    fs::create_directories(out);
    static const std::regex unit_re(R"re(^FLEET_UNIT="([^"]*)")re", std::regex::multiline);
    for (const auto& [src, given] : sources) {
        auto body = read_file(src);
        if (!body) {
            std::cout << "capture: no " << src.string()
                      << " to record; this session is unattributed" << std::endl;
            continue;
        }
        std::string name = given;
        if (name.empty()) {
            std::smatch m;
            name = std::regex_search(*body, m, unit_re) ? m[1].str() : src.filename().string();
        }
        // The temp name comes from mkstemp, not the pid: the camera and the accel reader
        // write the same manifests from separate containers, where both are pid 2, so a
        // pid-named temp was one file. One renamed it away and the other's rename threw;
        // campod-nw 2026-10-29, `cannot rename ... .fleet-image.2`.
        std::string tmpl = (out / ("." + name + ".XXXXXX")).string();
        const int fd = ::mkstemp(tmpl.data());
        if (fd < 0) {
            std::cout << "capture: cannot create a temp file for " << name << " in " << out.string()
                      << "; this session is unattributed" << std::endl;
            continue;
        }
        const bool wrote = ::write(fd, body->data(), body->size()) == static_cast<ssize_t>(body->size());
        ::fchmod(fd, 0644);
        ::close(fd);
        std::error_code ec;
        if (wrote) fs::rename(tmpl, out / name, ec);
        if (!wrote || ec) {
            std::cout << "capture: cannot place " << name << " in " << out.string()
                      << (ec ? ": " + ec.message() : "") << "; this session is unattributed" << std::endl;
            fs::remove(tmpl, ec);
        }
    }
}

bool capture_wanted(const std::optional<fs::path>& flag_path) {
    if (!flag_path) return true;
    auto body = read_file(*flag_path);
    if (!body) return false;
    const auto v = trim(*body);
    return v == "1" || v == "true" || v == "on" || v == "yes";
}

void write_status(const std::optional<fs::path>& path, const nlohmann::json& doc) {
    if (!path) return;
    try {
        const fs::path tmp = path->string() + ".tmp";
        {
            std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
            if (!f) return;
            f << doc.dump();
            if (!f) return;
        }
        fs::rename(tmp, *path);
    } catch (const std::exception&) {
    }
}

std::optional<fs::path> env_path(const char* name, const char* fallback) {
    const char* v = std::getenv(name);
    const std::string s = trim(v ? v : fallback);
    if (s.empty()) return std::nullopt;
    return fs::path(s);
}

std::vector<uint8_t> encode_pgm(const uint8_t* data, unsigned width, unsigned height,
                                unsigned stride) {
    const std::string header =
        "P5\n" + std::to_string(width) + " " + std::to_string(height) + "\n255\n";
    std::vector<uint8_t> out;
    out.reserve(header.size() + size_t{width} * height);
    out.insert(out.end(), header.begin(), header.end());
    for (unsigned row = 0; row < height; ++row) {
        const uint8_t* p = data + size_t{row} * stride;
        out.insert(out.end(), p, p + width);
    }
    return out;
}

}  // namespace capture
