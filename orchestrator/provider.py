"""A native model adapter shared by the workflow worker and interactive chat."""
import threading
import json
from .config import Generation
from .decision_policy import admit_decision
from .generation_policy import resolve_sampling
from .ollama import OllamaClient, _context_window
from .model_policy import remote_model
from .ollama import _strict_json
from .strata import StrataClient
from .llama_cpp import LlamaCppClient


class NativeProvider:
    def __init__(self, config):
        self.ollama = (StrataClient(config.strata) if config.backend == "strata"
                       else LlamaCppClient(config.llama_cpp) if config.backend == "llama-cpp"
                       else OllamaClient(config.ollama_url))
        self.config = config

    def identity(self):
        if self.config.backend in {"strata", "llama-cpp"}:
            return self.ollama.identity()
        return {"name": "ollama", "endpoint": self.config.ollama_url}

    def admit(self, model, settings, payload, stage):
        if self.config.backend == "llama-cpp":
            if stage == "decider":
                raise ValueError("The native non-thinking llama.cpp profile does not implement System One")
            generation = Generation.model_validate(settings)
            self.ollama._preflight(model, payload["messages"],
                                  {"num_ctx": generation.context, "num_predict": generation.output_tokens or -1},
                                  generation.think, payload["schema"], threading.Event(), None)
            return
        if stage == "decider":
            if self.config.backend == "strata":
                identity = self.ollama.identity()
                required = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 2048
                if model != identity["model"] or required > identity["context"]:
                    raise ValueError("Strata decision input does not fit the pinned loaded model")
                return
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
        overrides = {k: getattr(generation, k) for k in (
            "temperature", "top_k", "top_p", "min_p", "presence_penalty",
            "frequency_penalty", "repetition_penalty",
        )}
        sampling = ({k: v for k, v in overrides.items() if v is not None}
                    if self.config.backend in {"strata", "llama-cpp"}
                    else resolve_sampling(model, generation.think, overrides))
        options = {**sampling,
                   "num_ctx": generation.context, "num_predict": generation.output_tokens or -1}
        stream = self.ollama.stream_chat(model, payload["messages"], options, think=generation.think,
                                         cancel=cancel, deadline=deadline, format_schema=payload["schema"],
                                         repetition_guard=generation.repetition_guard)
        final = None
        content = []
        reasoning = []
        size = 0
        try:
            for frame in stream:
                part = frame.get("message", {}).get("content", "")
                thought = frame.get("message", {}).get("thinking", "")
                size += len(part.encode())
                size += len(thought.encode())
                if size > 4_000_000:
                    raise ValueError("Model output exceeds the response bound")
                content.append(part)
                reasoning.append(thought)
                if frame["done"]:
                    final = frame
        finally:
            stream.close()
        if final is None:
            raise RuntimeError("Native completion unknown")
        if self.config.backend == "llama-cpp":
            # This includes exact bounded transport bytes before any stage schema
            # validation; a malformed final JSON object is still a saved result.
            return final["llama_cpp_receipt"]
        message = {"content": "".join(content)}
        if any(reasoning):
            message["reasoning_content"] = "".join(reasoning)
        result = {"model": model,
                "choices": [{"finish_reason": final.get("done_reason"), "message": message}],
                "usage": {"prompt_tokens": final.get("prompt_eval_count"),
                          "completion_tokens": final.get("eval_count")},
                "durations_ns": {k: final[k] for k in ("load_duration","eval_duration","total_duration") if k in final},
                "native": ({"backend": "strata", "attempts": final.get("strata_native_attempts"),
                            "terminal_state": final.get("done_reason")}
                           if self.config.backend == "strata" else {"backend": "ollama"})}
        if self.config.backend == "strata" and isinstance(final.get("strata_native_transport"), dict):
            result["native_transport"] = final["strata_native_transport"]
        return result

    def decide(self, model, payload, cancel, deadline):
        if self.config.backend == "llama-cpp":
            raise ValueError("The native non-thinking llama.cpp profile does not implement System One")
        if self.config.backend == "strata":
            properties = {}
            required = []
            for name, question in payload["questions"].items():
                choices = list(question["criteria"])
                properties[name] = {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["choice"]},
                        "choice": {"type": "string", "enum": choices},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "probabilities": {
                            "type": "object",
                            "properties": {choice: {"type": "number", "minimum": 0, "maximum": 1}
                                           for choice in choices},
                            "required": choices,
                            "additionalProperties": False,
                        },
                    },
                    "required": ["type", "choice", "confidence", "probabilities"],
                    "additionalProperties": False,
                }
                required.append(name)
            schema = {"type": "object", "properties": {"answers": {
                "type": "object", "properties": properties, "required": required,
                "additionalProperties": False,
            }}, "required": ["answers"], "additionalProperties": False}
            messages = [
                {"role": "system", "content": "Choose only from the supplied criteria and return calibrated probabilities."},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            settings = self.config.generation_for("decider").model_dump()
            raw = self.complete(model, settings, {"messages": messages, "schema": schema}, cancel, deadline)
            choices = raw.get("choices") or []
            content = choices[0].get("message", {}).get("content") if len(choices) == 1 else None
            value = _strict_json(content) if isinstance(content, str) else None
            if not isinstance(value, dict) or not isinstance(value.get("answers"), dict):
                raise ValueError("Strata returned an invalid decision")
            return {**raw, "answers": value["answers"]}
        if cancel.is_set():
            raise RuntimeError("Run cancelled")
        _, raw = self.ollama.system_one(model, payload["state"], payload["questions"], deadline=deadline)
        return raw
