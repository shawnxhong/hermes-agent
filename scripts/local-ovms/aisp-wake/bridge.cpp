// Thin C ABI around the supplied AISP live-asr pipeline; no agent/TTS nodes.
#include "aisp.h"
#include <cstdio>
#include <memory>
#include <vector>
#include <atomic>

using Callback = void (*)(int64_t, uint32_t, const char*);
struct Bridge {
    aisp::Runtime runtime;
    aisp::Pipeline::Ptr pipeline;
    aisp::MediaSession::Ptr session;
    aisp::MediaSession::Callback callback;
    std::atomic<bool> capture_alive{false};
};
extern "C" {
void* wake_create(const char* model, const char* device, const char* context, Callback cb) {
    try {
        auto b = std::make_unique<Bridge>();
        ov::AnyMap props;
        props["asr.model"] = std::string(model);
        props["asr.device"] = std::string(device);
        props["asr.context"] = std::string(context);
        props["vad.device"] = std::string("CPU");
        props["source_language"] = aisp::language::Type::En;
        props["diar.enabled"] = false;
        b->pipeline = b->runtime.create_pipeline("live-asr", props);
        if (b->pipeline->get_property(std::string("source_language")).as<aisp::language::Type>() != aisp::language::Type::En)
            throw std::runtime_error("SDK did not retain source_language=en");
        if (b->pipeline->get_property(std::string("asr.context")).as<std::string>() != context)
            throw std::runtime_error("SDK did not retain asr.context");
        std::fprintf(stderr,"Qwen3 live-ASR: device=%s source_language=en (English) context=%s\n",device,context);
        b->callback = [cb](ov::AnyMap&& out) {
            if (aisp::keys::output_text.exists_in(out)) {
                auto text = aisp::keys::output_text.get_from<std::string>(out);
                cb(aisp::keys::output_timestamp.get_from<int64_t>(out),
                   aisp::keys::output_flag.get_from<uint32_t>(out), text.c_str());
            }
            return true;
        };
        b->pipeline->set_callback(b->callback);
        return b.release();
    } catch(const std::exception& e) { std::fprintf(stderr,"wake_create: %s\n",e.what()); return nullptr; }
}
int wake_feed(void* raw, const float* data, size_t n, int64_t timestamp) {
    try {
        ov::AnyMap in;
        in["input.audio_samples"] = std::vector<float>(data, data+n);
        in["input.timestamp"] = timestamp;
        static_cast<Bridge*>(raw)->pipeline->forward_async(in);
        return 0;
    } catch (...) { return -1; }
}
int wake_flush(void* raw) {
    try { static_cast<Bridge*>(raw)->pipeline->wait(); return 0; }
    catch (...) { return -1; }
}
int wake_start_capture(void* raw, const char* source) {
    try {
        auto b = static_cast<Bridge*>(raw);
        ov::AnyMap config;
        config["audio.source"] = std::string(source);
        config["audio.chunk_ms"] = int32_t(500);
        b->session = aisp::MediaSession::create(b->pipeline, config);
        b->session->set_callback(b->callback);
        b->session->set_completion_callback([b](aisp::SessionRunResult) { b->capture_alive = false; });
        b->capture_alive = true;
        b->session->start();
        std::fprintf(stderr,"Continuous microphone capture started; ASR device configured by service (default GPU)\n");
        return 0;
    } catch(const std::exception& e) { std::fprintf(stderr,"capture: %s\n",e.what()); return -1; }
}
int wake_capture_alive(void* raw) { return static_cast<Bridge*>(raw)->capture_alive ? 1 : 0; }
void wake_destroy(void* raw) { delete static_cast<Bridge*>(raw); }
}
