#include "session.hpp"

#include <cmath>
#include <iostream>

#include "conventions.hpp"

namespace capture {

namespace {

double mono_s() { return clock_triple().mono_ns / 1e9; }
double round3(double v) { return std::round(v * 1000.0) / 1000.0; }

}  // namespace

Session::Session(SessionConfig cfg) : cfg_(std::move(cfg)), writer_(cfg_.dir.string()) {
    for (const auto& s : cfg_.streams) streams_[s];
    // The campods' status document, so pod-link publishes it the same way and `ready`
    // means what a pod's does: will arming now produce frames.
    state_ = {
        {"node", cfg_.node},
        {"session", cfg_.session},
        {"camera", "waiting"},
        {"phase", "startup"},
        {"ready", false},
        {"ready_at_mono_s", nullptr},
        {"warm", false},
        {"warm_at_mono_s", nullptr},
        {"capturing", false},
        {"frames", 0},
        {"last_frame_mono_s", nullptr},
    };
}

Session::~Session() { stop(); }

void Session::set_status(const std::string& key, nlohmann::json value) { state_[key] = std::move(value); }

void Session::publish() {
    // No sync in this loop, so ready is warm: every stream has delivered its warmup.
    state_["ready"] = warm_;
    if (warm_ && state_["ready_at_mono_s"].is_null()) state_["ready_at_mono_s"] = round3(mono_s());
    last_publish_ = mono_s();
    state_["as_of_mono_s"] = round3(last_publish_);
    write_status(cfg_.status_path, state_);
}

void Session::begin(const std::string& file_prefix) {
    prefix_ = file_prefix;
    // Every frame retrieved, kept or not: what shows the streams' timing without
    // writing a single image.
    const auto path = cfg_.dir / (prefix_ + "_" + cfg_.session + ".frames.jsonl");
    ledger_ = std::fopen(path.c_str(), "a");
    if (!ledger_) throw std::runtime_error("cannot open " + path.string());
    state_["phase"] = "warmup";
    publish();
}

void Session::tick(double now) {
    if (now - last_gate_ >= 0.1) {
        last_gate_ = now;
        if (!warm_) {
            bool all = true;
            for (const auto& [kind, c] : streams_) all = all && c.seen >= cfg_.warmup_frames;
            if (all) {
                warm_ = true;
                armed_ = capture_wanted(cfg_.flag_path);
                state_["warm"] = true;
                state_["warm_at_mono_s"] = round3(now);
                state_["phase"] = "capture";
                state_["capturing"] = capturing();
                std::cout << cfg_.log_name << ": WARM at " << now << " s, "
                          << (armed_ ? "capturing" : "paused") << std::endl;
                publish();
            }
        } else if (const bool wanted = capture_wanted(cfg_.flag_path); wanted != armed_) {
            armed_ = wanted;
            state_["capturing"] = capturing();
            std::cout << cfg_.log_name << ": " << (armed_ ? "resumed" : "paused") << " at " << now << " s"
                      << std::endl;
            if (ledger_) std::fflush(ledger_);
            publish();
        }
    }
    if (now - last_publish_ >= 1.0) {
        nlohmann::json seen, gaps;
        for (const auto& [kind, c] : streams_) {
            seen[kind] = c.seen;
            gaps[kind] = c.device_seq_gaps;
        }
        state_["frames_seen"] = seen;
        state_["device_seq_gaps"] = gaps;
        state_["writer_dropped"] = writer_.dropped();
        publish();
    }
}

void Session::frame(const Frame& f, const char* ext, const std::function<std::vector<uint8_t>()>& encode) {
    auto& c = streams_.at(f.kind);
    const auto clk = clock_triple();
    const int64_t wall_ns = mono_to_wall_ns(f.monotonic_ns, clk);
    if (c.last_device_seq && f.device_seq != *c.last_device_seq + 1) c.device_seq_gaps++;
    c.last_device_seq = f.device_seq;
    c.seen++;
    const bool keep = capturing();

    nlohmann::json rec = {{"kind", f.kind}, {"device_seq", f.device_seq}, {"monotonic_ns", f.monotonic_ns}};
    rec.update(f.fields);
    rec["kept"] = keep;
    if (ledger_) {
        const std::string s = rec.dump();
        std::fwrite(s.data(), 1, s.size(), ledger_);
        std::fputc('\n', ledger_);
    }
    if (!keep) return;

    char seqbuf[24];
    std::snprintf(seqbuf, sizeof(seqbuf), "%08lld", static_cast<long long>(c.kept));
    const std::string stem = prefix_ + "_" + f.kind + "_" + seqbuf + "_" + utc_stem(wall_ns);
    nlohmann::json sidecar = {
        {"node", cfg_.node},
        {"kind", f.kind},
        {"seq", c.kept},
        {"file", stem + ext},
        {"device_seq", f.device_seq},
        {"width", f.width},
        {"height", f.height},
        {"monotonic_ns", f.monotonic_ns},
        {"wall_ns", wall_ns},
        {"wall_clock_unix", wall_ns / 1e9},
        {"wall_clock_utc", utc_iso(wall_ns)},
        {"clk", to_json(clk)},
    };
    sidecar.update(f.fields);

    Job job;
    job.path = (cfg_.dir / (stem + ext)).string();
    job.bytes = encode();
    job.sidecar_path = (cfg_.dir / (stem + ".json")).string();
    job.sidecar = sidecar.dump();
    if (writer_.submit(std::move(job))) {
        c.kept++;
        // `frames` is the first stream's count, the one a panel shows.
        if (!cfg_.streams.empty() && f.kind == cfg_.streams.front()) state_["frames"] = c.kept;
        state_["last_frame_mono_s"] = round3(mono_s());
    }
}

void Session::stop() {
    if (stopped_) return;
    stopped_ = true;
    writer_.stop();
    if (ledger_) {
        std::fclose(ledger_);
        ledger_ = nullptr;
    }
    warm_ = false;
    armed_ = false;
    state_["phase"] = failed_ ? "failed" : "stopped";
    state_["warm"] = false;
    state_["capturing"] = false;
    publish();
    std::cout << cfg_.log_name << ": " << (failed_ ? "failed" : "stopped") << "; written";
    for (const auto& [kind, c] : streams_) std::cout << " " << kind << "=" << c.kept;
    std::cout << ", dropped by the writer " << writer_.dropped() << std::endl;
}

void Session::fail(const std::string& why) {
    std::cerr << cfg_.log_name << ": " << why << std::endl;
    failed_ = true;
    state_["error"] = why;
    stop();
}

}  // namespace capture
