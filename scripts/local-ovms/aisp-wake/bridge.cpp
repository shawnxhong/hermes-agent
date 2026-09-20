// Thin C ABI around the supplied AISP live-asr pipeline; no agent/TTS nodes.
#include "aisp.h"
#include <cstdio>
#include <memory>
#include <vector>

using Callback = void (*)(int64_t, uint32_t, const char*);
struct Bridge {
    aisp::Runtime runtime;
    aisp::Pipeline::Ptr pipeline;
};
extern "C" {
void* wake_create(const char* model, const char* device, Callback cb) {
    try {
        auto b = std::make_unique<Bridge>();
        ov::AnyMap props;
        props["asr.model"] = std::string(model);
        props["asr.device"] = std::string(device);
        props["vad.device"] = std::string("CPU");
        props["source_language"] = aisp::language::Type::En;
        props["diar.enabled"] = false;
        b->pipeline = b->runtime.create_pipeline("live-asr", props);
        b->pipeline->set_callback([cb](ov::AnyMap&& out) {
            if (aisp::keys::output_text.exists_in(out)) {
                auto text = aisp::keys::output_text.get_from<std::string>(out);
                cb(aisp::keys::output_timestamp.get_from<int64_t>(out),
                   aisp::keys::output_flag.get_from<uint32_t>(out), text.c_str());
            }
            return true;
        });
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
void wake_destroy(void* raw) { delete static_cast<Bridge*>(raw); }
}
