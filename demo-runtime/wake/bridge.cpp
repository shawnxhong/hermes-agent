// Thin C ABI around the supplied AISP live-asr pipeline; no agent/TTS nodes.
#include "aisp.h"
#include "aisp_version.h"
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
uint32_t wake_eos_flag() { return AISP_FLAG_EOS; }
void* wake_create_with_split(const char* model, const char* device, const char* context, const char* language, float vad_threshold, float block_rms_threshold, int32_t split_sensitivity, Callback cb) {
    try {
        if (split_sensitivity < 0 || split_sensitivity > 2)
            throw std::runtime_error("Invalid vad.split_sensitivity");
        auto b = std::make_unique<Bridge>();
        ov::AnyMap props;
        props["asr.model"] = std::string(model);
        props["asr.device"] = std::string(device);
        props["asr.context"] = std::string(context);
        props["asr.max_new_tokens"] = int32_t(128);
        props["vad.device"] = std::string("CPU");
        // Init config is forwarded to VADStreamingNode. live-asr set_property
        // does not forward VAD keys: changing these requires pipeline recreation.
        props["vad.threshold"] = vad_threshold;
        // Private SDK 0.4.1 key: RMS of VAD frame probabilities, not PCM energy.
        props["vad.block_rms_threshold"] = block_rms_threshold;
        props["vad.split_sensitivity"] = split_sensitivity;
        auto lang = aisp::language::try_parse_code(language);
        if (!lang) throw std::runtime_error("Invalid source_language");
        props["source_language"] = *lang;
        props["diar.enabled"] = false;
        std::fprintf(stderr, "AISP SDK %s; asr.max_new_tokens=128\n", AISP_VERSION_STR);
        b->pipeline = b->runtime.create_pipeline("live-asr", props);
        if (b->pipeline->get_property(std::string("source_language")).as<aisp::language::Type>() != *lang)
            throw std::runtime_error("SDK did not retain source_language");
        if (b->pipeline->get_property(std::string("asr.context")).as<std::string>() != context)
            throw std::runtime_error("SDK did not retain asr.context");
        std::fprintf(stderr,"Qwen3 live-ASR: device=%s source_language=%s context=%s\n",device,language,context);
        for (const auto& key : {"vad.threshold", "vad.block_rms_threshold"})
            if (b->pipeline->get_property(std::string(key)).as<float>() != props[key].as<float>())
                throw std::runtime_error("SDK did not retain VAD initialization config");
        std::fprintf(stderr, "VAD init: threshold=%.3f block_rms_threshold=%.3f\n",
                     vad_threshold, block_rms_threshold);
        if (b->pipeline->get_property(std::string("vad.split_sensitivity")).as<int32_t>() != split_sensitivity)
            throw std::runtime_error("SDK did not retain vad.split_sensitivity");
        std::fprintf(stderr, "VAD init: split_sensitivity=%s (%d)\n",
                     split_sensitivity == 0 ? "low" : split_sensitivity == 1 ? "medium" : "high", split_sensitivity);
        b->callback = [cb](ov::AnyMap&& out) {
            // Final EOS can be a separate callback with no output_text key.
            if (aisp::keys::output_timestamp.exists_in(out) && aisp::keys::output_flag.exists_in(out)) {
                auto text = aisp::keys::output_text.exists_in(out)
                    ? aisp::keys::output_text.get_from<std::string>(out) : std::string{};
                cb(aisp::keys::output_timestamp.get_from<int64_t>(out),
                   aisp::keys::output_flag.get_from<uint32_t>(out), text.c_str());
            }
            return true;
        };
        b->pipeline->set_callback(b->callback);
        return b.release();
    } catch(const std::exception& e) { std::fprintf(stderr,"wake_create: %s\n",e.what()); return nullptr; }
}
// Retain the old ABI for diagnostic clients; new Python requires the new symbol.
void* wake_create(const char* model, const char* device, const char* context, const char* language, float vad_threshold, float block_rms_threshold, Callback cb) {
    return wake_create_with_split(model, device, context, language, vad_threshold, block_rms_threshold, 1, cb);
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
int wake_start_capture(void* raw, const char* source, const char* aec, const char* reference) {
    try {
        auto b = static_cast<Bridge*>(raw);
        ov::AnyMap config;
        config["audio.source"] = std::string(source);
        config["audio.aec"] = std::string(aec);
        if (std::string(aec) != "none") config["audio.aec_reference"] = std::string(reference);
        config["audio.chunk_ms"] = int32_t(500);
        // Preserve measured 0.3.6 capture policy across SDK default changes.
        config["audio.max_queue_ms"] = int32_t(2000);
        config["audio.render_queue_ms"] = int32_t(4000);
        b->session = aisp::MediaSession::create(b->pipeline, config);
        b->session->set_callback(b->callback);
        b->session->set_completion_callback([b](aisp::SessionRunResult) { b->capture_alive = false; });
        b->capture_alive = true;
        b->session->start();
        std::fprintf(stderr,"Continuous microphone capture started; ASR device configured by service (default GPU)\n");
        return 0;
    } catch(const std::exception& e) { std::fprintf(stderr,"capture: %s\n",e.what()); return -1; }
}
int wake_stop_capture(void* raw) {
    auto b = static_cast<Bridge*>(raw);
    try {
        if (b->session) b->session->stop();
        b->session.reset();
        b->capture_alive = false;
        return 0;
    } catch(const std::exception& e) { std::fprintf(stderr,"stop_capture: %s\n",e.what()); return -1; }
}
int wake_enumerate_playback(void (*cb)(int32_t, const char*)) {
    try {
        for (const auto& device : aisp::enumerate_audio_devices(aisp::AudioDeviceKind::Playback))
            cb(device.id, device.name.c_str());
        return 0;
    } catch(const std::exception& e) { std::fprintf(stderr,"enumerate: %s\n",e.what()); return -1; }
}
int wake_capture_alive(void* raw) { return static_cast<Bridge*>(raw)->capture_alive ? 1 : 0; }
void wake_destroy(void* raw) { delete static_cast<Bridge*>(raw); }
}
