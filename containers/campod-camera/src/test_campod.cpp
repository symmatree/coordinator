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

    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "test_campod: all checks passed\n";
    return 0;
}
