// campod.hpp without a camera. Run by the image build, which fails if this does.
#include <iostream>
#include <stdexcept>

#include "campod.hpp"

static int failures = 0;

#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            std::cerr << __FILE__ << ":" << __LINE__ << ": FAILED " #cond << "\n"; \
            ++failures;                                                          \
        }                                                                        \
    } while (0)

template <typename F>
static bool throws(F f) {
    try {
        f();
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

int main() {
    // The leader paces; every other pod follows; nobody named is a broken deployment.
    CHECK(campod::sync_mode_for("campod-sw", "campod-sw") == "server");
    CHECK(campod::sync_mode_for("campod-sw", "campod-se") == "client");
    CHECK(throws([] { campod::sync_mode_for("", "campod-se"); }));

    CHECK(campod::focus_dioptres("infinity") == 0.0f);
    CHECK(campod::focus_dioptres("") == 0.0f);
    CHECK(campod::focus_dioptres("0.8") == 0.8f);
    CHECK(throws([] { campod::focus_dioptres("near"); }));
    CHECK(throws([] { campod::focus_dioptres("0.8m"); }));
    CHECK(throws([] { campod::focus_dioptres("-1"); }));

    CHECK(campod::second_offset_ns(5'250'000'000) == 250'000'000);
    CHECK(campod::second_offset_ns(5'000'000'000) == 0);
    CHECK(campod::second_offset_ns(-250'000'000) == 750'000'000);

    // An APP1 whose length field covers it, and the JPEG's own bytes after it unchanged.
    const auto app1 = campod::exif_app1({"Arducam", "campod-ne", 1'793'000'000'250'000'000, 2.87, 16});
    CHECK(app1[0] == 0xFF && app1[1] == 0xE1);
    CHECK(static_cast<size_t>(app1[2] << 8 | app1[3]) == app1.size() - 2);
    CHECK(std::string(app1.begin() + 4, app1.begin() + 10) == std::string("Exif\0\0", 6));
    const std::string text(app1.begin(), app1.end());
    CHECK(text.find("2026:10:26 07:33:20") != std::string::npos);
    CHECK(text.find("250000") != std::string::npos);
    CHECK(text.find("campod-ne") != std::string::npos);
    const std::vector<uint8_t> jpeg = {0xFF, 0xD8, 0xFF, 0xE0, 0xFF, 0xD9};
    const auto stamped = campod::with_app1(jpeg, app1);
    CHECK(stamped.size() == jpeg.size() + app1.size());
    CHECK(stamped[2] == 0xFF && stamped[3] == 0xE1);
    CHECK(std::vector<uint8_t>(stamped.end() - 4, stamped.end()) == std::vector<uint8_t>(jpeg.begin() + 2, jpeg.end()));
    CHECK(throws([&] { campod::with_app1({0x00, 0x01}, app1); }));

    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "test_campod: all checks passed\n";
    return 0;
}
