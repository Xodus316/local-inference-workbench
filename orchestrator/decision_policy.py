"""Admission and confidence gates for retained PPLX System One routes."""
from __future__ import annotations

import json
import math

from .decision_context import configured_context, loaded_context
from .model_policy import DECISION_32K, DECISION_64K, DECISION_CONFIDENCE_MINIMUM, remote_model
from .validation import ValidationError


def admit_decision(ollama, state, questions, *, model=None):
    required = len(json.dumps({'state': state, 'questions': questions}, ensure_ascii=False).encode('utf-8')) + 2048
    selected = model or (DECISION_32K if required <= 32768 else DECISION_64K)
    window = 32768 if selected == DECISION_32K else 65536
    if required > window:
        error = ValidationError('Complete decision evidence exceeds the supported context')
        error.diagnostics = {'failure_category': 'context_limit_exceeded', 'required_context': required,
                             'context_window': window, 'model': selected}
        raise error
    installed = {row.get('name', row.get('model')): row for row in ollama.models() if isinstance(row, dict)}
    if selected not in installed:
        error = ValidationError('Required PPLX context alias is not installed')
        error.diagnostics = {'failure_category': 'model_unavailable', 'missing_models': [selected]}
        raise error
    if remote_model(selected, installed[selected]):
        raise ValidationError("A local decision model is required")
    metadata = ollama.show(selected)
    if (not isinstance(metadata, dict) or metadata.get('remote_host') or metadata.get('remote_model')
            or 'decision' not in metadata.get('capabilities', [])):
        raise ValidationError('Required alias must advertise local decision support')
    try:
        configured = configured_context(metadata)
        runtime = loaded_context(ollama.loaded_models(), selected)
    except ValueError:
        raise ValidationError('PPLX context metadata is invalid') from None
    if configured is None or configured < window or runtime is not None and runtime < window:
        error = ValidationError('Configured or loaded PPLX context is too small')
        error.diagnostics = {'failure_category': 'context_limit_exceeded', 'model': selected,
                             'required_context': window, 'configured_context_window': configured,
                             'runtime_context_window': runtime}
        raise error
    return selected, window


def require_confident(answers):
    if not answers or any(type(answer.get('confidence')) not in (int, float)
                          or not math.isfinite(answer['confidence'])
                          or not DECISION_CONFIDENCE_MINIMUM <= answer['confidence'] <= 1
                          for answer in answers.values()):
        error = ValidationError('PPLX decision is unresolved. Human review is required; no further model was invoked.')
        error.diagnostics = {'failure_category': 'human_review_required', 'automatic_retry': False}
        raise error
