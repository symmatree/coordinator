// The campod-specific decisions in capture.cpp that need no camera, so they test without one.
#pragma once

#include <cstdint>
#include <cstdio>
#include <ctime>
#include <stdexcept>
#include <string>
#include <vector>

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

// What each still carries in its EXIF, for tools that read the JPEG and not the sidecar.
// Model is the pod, so a reader that tells cameras apart by make and model (OpenSfM fits one
// set of intrinsics per camera) sees four. The time is the sidecar's exposure_wall_ns, UTC.
struct ExifFields {
    std::string make;
    std::string model;
    int64_t exposure_wall_ns;
    double focal_length_mm;
    int focal_length_35mm;  // OpenSfM's starting focal is this over 36, so width-based
};

// The EXIF block as a JPEG APP1 segment, marker included: IFD0 with Make, Model and the
// Exif IFD pointer; the Exif IFD with DateTimeOriginal, OffsetTimeOriginal, FocalLength,
// SubSecTimeOriginal (microseconds) and FocalLengthIn35mmFilm. Little-endian TIFF.
inline std::vector<uint8_t> exif_app1(const ExifFields& e) {
    struct Entry {
        uint16_t tag, type;
        uint32_t count;
        std::vector<uint8_t> value;
    };
    constexpr uint16_t ASCII = 2, SHORT = 3, LONG = 4, RATIONAL = 5;
    auto le = [](std::vector<uint8_t>& b, uint32_t v, int n) {
        for (int i = 0; i < n; ++i) b.push_back(static_cast<uint8_t>(v >> (8 * i)));
    };
    auto ascii = [](uint16_t tag, const std::string& s) {
        std::vector<uint8_t> v(s.begin(), s.end());
        v.push_back(0);
        return Entry{tag, ASCII, static_cast<uint32_t>(v.size()), v};
    };
    auto ifd_size = [](const std::vector<Entry>& es) {
        size_t n = 2 + 12 * es.size() + 4;
        for (const auto& x : es)
            if (x.value.size() > 4) n += (x.value.size() + 1) & ~size_t{1};
        return n;
    };
    // Appends one IFD at the end of `tiff`, its out-of-line values straight after it.
    auto write_ifd = [&](std::vector<uint8_t>& tiff, const std::vector<Entry>& es) {
        const size_t data = tiff.size() + 2 + 12 * es.size() + 4;
        std::vector<uint8_t> tail;
        le(tiff, static_cast<uint32_t>(es.size()), 2);
        for (const auto& x : es) {
            le(tiff, x.tag, 2);
            le(tiff, x.type, 2);
            le(tiff, x.count, 4);
            if (x.value.size() <= 4) {
                std::vector<uint8_t> v = x.value;
                v.resize(4, 0);
                tiff.insert(tiff.end(), v.begin(), v.end());
            } else {
                le(tiff, static_cast<uint32_t>(data + tail.size()), 4);
                tail.insert(tail.end(), x.value.begin(), x.value.end());
                if (tail.size() % 2) tail.push_back(0);
            }
        }
        le(tiff, 0, 4);  // no next IFD
        tiff.insert(tiff.end(), tail.begin(), tail.end());
    };

    constexpr int64_t s = 1'000'000'000;
    const int64_t sub = second_offset_ns(e.exposure_wall_ns);
    const std::time_t secs = static_cast<std::time_t>((e.exposure_wall_ns - sub) / s);
    std::tm tm{};
    gmtime_r(&secs, &tm);
    char when[20], subsec[24];
    std::strftime(when, sizeof when, "%Y:%m:%d %H:%M:%S", &tm);
    std::snprintf(subsec, sizeof subsec, "%06lld", static_cast<long long>(sub / 1000));

    std::vector<uint8_t> focal, focal35;
    le(focal, static_cast<uint32_t>(e.focal_length_mm * 1000 + 0.5), 4);
    le(focal, 1000, 4);
    le(focal35, static_cast<uint32_t>(e.focal_length_35mm), 2);
    const std::vector<Entry> exif_ifd = {
        ascii(0x9003, when),           // DateTimeOriginal
        ascii(0x9011, "+00:00"),       // OffsetTimeOriginal
        {0x920A, RATIONAL, 1, focal},  // FocalLength
        ascii(0x9291, subsec),         // SubSecTimeOriginal
        {0xA405, SHORT, 1, focal35},   // FocalLengthIn35mmFilm
    };
    std::vector<Entry> ifd0 = {ascii(0x010F, e.make), ascii(0x0110, e.model), {0x8769, LONG, 1, {}}};
    le(ifd0[2].value, static_cast<uint32_t>(8 + ifd_size(ifd0)), 4);  // the Exif IFD follows IFD0

    std::vector<uint8_t> tiff = {'I', 'I', 0x2A, 0x00};
    le(tiff, 8, 4);
    write_ifd(tiff, ifd0);
    write_ifd(tiff, exif_ifd);

    const size_t length = 2 + 6 + tiff.size();
    if (length > 0xFFFF) throw std::invalid_argument("EXIF block too long for one APP1");
    std::vector<uint8_t> app1 = {0xFF, 0xE1, static_cast<uint8_t>(length >> 8), static_cast<uint8_t>(length),
                                 'E', 'x', 'i', 'f', 0, 0};
    app1.insert(app1.end(), tiff.begin(), tiff.end());
    return app1;
}

// The JPEG with `app1` straight after its SOI marker, where EXIF readers look for it.
inline std::vector<uint8_t> with_app1(const std::vector<uint8_t>& jpeg, const std::vector<uint8_t>& app1) {
    if (jpeg.size() < 2 || jpeg[0] != 0xFF || jpeg[1] != 0xD8) throw std::invalid_argument("not a JPEG");
    std::vector<uint8_t> out(jpeg.begin(), jpeg.begin() + 2);
    out.insert(out.end(), app1.begin(), app1.end());
    out.insert(out.end(), jpeg.begin() + 2, jpeg.end());
    return out;
}

}  // namespace campod
