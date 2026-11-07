"""A native model adapter shared by the workflow worker and interactive chat."""
import threading
from .config import Generation
from .decision_policy import admit_decision
from .generation_policy import resolve_sampling
from .ollama import OllamaClient, _context_window
from .model_policy import remote_model


class NativeProvider:
    def __init__(self, config):
        self.ollama = OllamaClient(config.ollama_url)
        self.config = config

    def admit(self, model, settings, payload, stage):
        if stage == "decider":
            admit_decision(self.ollama, payload["state"], payload["questions"], model=model)
            return
        entry = next((m for m in self.ollama.models() if m.get("name", m.get("model")) == model), None)
        if entry is None:
            raise ValueError("Install the configured model in Ollama")
        if remote_model(model, entry):
            raise ValueError("This service requires a locally installed model")
        metadata = self.ollama.show(model)
        if remote_model(model, metadata):
            raise ValueError("This service requires a locally installed model")
        if "capabilities" in metadata and "completion" not in metadata["capabilities"]:
            raise ValueError("This model does not support completion")
        generation = Generation.model_validate(settings)
        _context_window(payload["messages"], generation.output_tokens or 1, generation.context)
        limits = [v for k, v in metadata.get("model_info", {}).items()
                  if k.endswith(".context_length") and type(v) is int and v > 0]
        if limits and generation.context > max(limits):
            raise ValueError("Configured context exceeds this model's advertised capacity")

    def complete(self, model, settings, payload, cancel: threading.Event, deadline):
        generation = Generation.model_validate(settings)
        overrides = {k: getattr(generation, k) for k in ("temperature", "top_k", "top_p")}
        options = {**resolve_sampling(model, generation.think, overrides),
                   "num_ctx": generation.context, "num_predict": generation.output_tokens or -1}
        stream = self.ollama.stream_chat(model, payload["messages"], options, think=generation.think,
                                         cancel=cancel, deadline=deadline, format_schema=payload["schema"])
        final = None
        content = []
        size = 0
        try:
            for frame in stream:
                part = frame.get("message", {}).get("content", "")
                size += len(part.encode())
                if size > 4_000_000:
                    raise ValueError("Model output exceeds the response bound")
                content.append(part)
                if frame["done"]:
                    final = frame
        finally:
            stream.close()
        if final is None:
            raise RuntimeError("Native completion unknown")
        return {"choices": [{"finish_reason": final.get("done_reason"),
                             "message": {"content": "".join(content)}}],
                "usage": {"prompt_tokens": final.get("prompt_eval_count"),
                          "completion_tokens": final.get("eval_count")},
                "durations_ns": {k: final[k] for k in ("load_duration","eval_duration","total_duration") if k in final}}

    def decide(self, model, payload, cancel, deadline):
        if cancel.is_set():
            raise RuntimeError("Run cancelled")
        _, raw = self.ollama.system_one(model, payload["state"], payload["questions"], deadline=deadline)
        return raw
