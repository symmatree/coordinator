// The capture conventions the campods established, for a C++ capture process.
//
// Each function here has a Python original in containers/campod-camera/capture.py, and the
// behaviour is the same on purpose: one session layout, one clock record, one gate file
// and one status file, whatever language wrote them. Free of depthai so it tests without
// a device (test_conventions.cpp), and so a C++ campod capture can take it as-is.
#pragma once

#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

namespace capture {

// One boot/mono/wall sample, read back to back. The same three keys as the campod
// sidecars and the accel reader's `t:"clk"` records.
struct ClockTriple {
    int64_t boot_ns;
    int64_t mono_ns;
    int64_t wall_ns;
};

ClockTriple clock_triple();
nlohmann::json to_json(const ClockTriple& c);

// A CLOCK_MONOTONIC instant as wall time, by this triple's offset.
inline int64_t mono_to_wall_ns(int64_t mono_ns, const ClockTriple& c) {
    return mono_ns + (c.wall_ns - c.mono_ns);
}

// "2026-10-26T20:12:31.123456Z", microseconds, UTC.
std::string utc_iso(int64_t wall_ns);
// "20261026T201231_123456Z", the filename form.
std::string utc_stem(int64_t wall_ns);

// The HOST's name: OVERRIDE if set, else the bind-mounted host /etc/hostname, else
// gethostname() (which in a container is the container id). capture.py _node_name.
std::string node_name(const char* override_env, const std::filesystem::path& host_hostname);

// The kernel boot id, which names the session.
std::string boot_id();

// Copy the build manifests into <session>/manifests/. Each source is (path, name); an
// empty name means "the FLEET_UNIT value in the file". A missing source is logged and
// skipped, never fatal. capture.py _copy_manifests.
void copy_manifests(const std::filesystem::path& session_dir,
                    const std::vector<std::pair<std::filesystem::path, std::string>>& sources);

// Should we be writing? No path means always. Otherwise the file's trimmed contents are
// one of 1/true/on/yes; absent or unreadable means paused. capture.py _capture_wanted.
bool capture_wanted(const std::optional<std::filesystem::path>& flag_path);

// Atomic temp-and-rename of a JSON document. Best-effort: a failure is swallowed, because
// no status problem is a reason to stop capturing. capture.py _write_status.
void write_status(const std::optional<std::filesystem::path>& path, const nlohmann::json& doc);

// An environment path where empty means "none" -- the campods' convention for the flag
// and status files.
std::optional<std::filesystem::path> env_path(const char* name, const char* fallback);

// Binary PGM (P5) of an 8-bit image whose rows are `stride` bytes apart.
std::vector<uint8_t> encode_pgm(const uint8_t* data, unsigned width, unsigned height,
                                unsigned stride);

}  // namespace capture
