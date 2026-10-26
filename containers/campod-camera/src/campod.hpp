// The campod-specific decisions in capture.cpp that need no camera, so they test without one.
#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>

namespace campod {

// server on the leader, client on everyone else. A leader is required: there is no
// unsynced mode, because an unsynced pod's frames cannot be used with its partner's.
// capture.py _sync_mode_for.
//
// One value for every pod, because the stack file is copied identically to each and a
// converge passes only device_role; each pod compares it against its own host name.
// Electing the leader over the bus instead is coordinator#501.
inline std::string sync_mode_for(const std::string& leader, const std::string& node) {
    if (leader.empty()) throw std::invalid_argument("no sync leader named");
    return leader == node ? "server" : "client";
}

// CAMPOD_STILL_FOCUS: "infinity", or a lens position in DIOPTRES (1/metres) -- not the
// OAK's 0-255 scale, where 125 would ask for 8 mm here.
inline float focus_dioptres(const std::string& focus) {
    if (focus.empty() || focus == "infinity") return 0.0f;
    size_t used = 0;
    const float d = std::stof(focus, &used);
    if (used != focus.size() || d < 0) throw std::invalid_argument("CAMPOD_STILL_FOCUS=" + focus);
    return d;
}

// How far into its wall-clock second an exposure fell, flooring, so every exposure belongs
// to the second it happened in. A field, not a decision: capture.py _boundary_offset_ns.
inline int64_t second_offset_ns(int64_t wall_ns) {
    constexpr int64_t s = 1'000'000'000;
    return ((wall_ns % s) + s) % s;
}

}  // namespace campod
