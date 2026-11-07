"""Shared sampling defaults for the workflow engine and chat.

Each approved run freezes its effective sampling settings.
"""

from __future__ import annotations

import copy

SAMPLING_POLICY = "model-sampling-v1"
EFFICIENT_COMPLETION = (
    " Resolve each ambiguity once with an explicit assumption consistent with the request."
    " Reconsider a decision only when new evidence reveals a contradiction."
    " Avoid repeating analysis; once the requested checks are complete, return the requested artifact."
    " Preserve every required behavior, artifact, evidence item and verification step."
    " Concision must not replace verification or omit required work."
)


def sampling_defaults(model: str, think: bool | str | None) -> dict:
    """The three supported sampling knobs from the model cards; unknowns stay native.

    Gemma 4: https://ai.google.dev/gemma/docs/core/model_card_4
    Qwen: https://huggingface.co/Qwen/Qwen3.8-27B
          https://huggingface.co/Qwen/Qwen3.5-9B
    None means model-default thinking, so use the thinking sampling preset.
    PPLX's decision protocol has no completion sampling policy.
    """
    family = model.lower().rsplit("/", 1)[-1].split(":", 1)[0]
    if family == "gemma4":
        return {"temperature": 1.0, "top_p": 0.95, "top_k": 64}
    if family in {"qwen3.5", "qwen3.8"}:
        return {"temperature": 0.7 if think is False else 1.0,
                "top_p": 0.8 if think is False else 0.95, "top_k": 20}
    if family.startswith("glm-4.7-flash"):
        return {"temperature": 1.0, "top_p": 0.95}
    if family.startswith("nvidia-nemotron-3.5-lightning"):
        return {"temperature": 1.0, "top_p": 0.95}
    return {}


def resolve_sampling(model: str, think: bool | str | None, overrides: dict | None = None) -> dict:
    """Fill missing knobs, preserving explicit overrides including zero."""
    return {**sampling_defaults(model, think),
            **{key: value for key, value in (overrides or {}).items() if value is not None}}


def workflow_options(model: str, think: bool | str | None) -> dict:
    return {"temperature": 1.0, **sampling_defaults(model, think), "repetition_guard": True}


def efficient_messages(messages: list[dict]) -> list[dict]:
    """Apply only in owned workflow builders, never to arbitrary API messages."""
    result = copy.deepcopy(messages)
    for message in result:
        if message.get("role") == "system":
            if EFFICIENT_COMPLETION not in message["content"]:
                message["content"] += EFFICIENT_COMPLETION
            break
    else:
        result.insert(0, {"role": "system", "content": EFFICIENT_COMPLETION.strip()})
    return result
