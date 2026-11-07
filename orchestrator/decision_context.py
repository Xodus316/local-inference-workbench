"""Keep configured System One context separate from loaded-runtime metadata."""

from __future__ import annotations

import re
from typing import Any


MAX_CONTEXT = 1_048_576
_CONFIGURED_ERROR = "Decision model configured context metadata is invalid"
_LOADED_ERROR = "Decision model loaded context metadata is invalid or ambiguous"


def configured_context(metadata: Any) -> int | None:
    """Read one explicit num_ctx from native /api/show parameters text.

    Missing configuration is unknown, rather than the model's training limit.
    Other parameter lines are inert and are never included in an exception.
    """
    if not isinstance(metadata, dict):
        raise ValueError(_CONFIGURED_ERROR)
    if "parameters" not in metadata:
        return None
    parameters = metadata["parameters"]
    if not isinstance(parameters, str):
        raise ValueError(_CONFIGURED_ERROR)
    result = None
    seen = False
    for line in parameters.splitlines():
        fields = line.split()
        if not fields:
            continue
        key = fields[0]
        if key != "num_ctx":
            # A malformed declaration must not silently become 'not configured'.
            if re.match(r"(?i)num_ctx(?:\b|_)", key):
                raise ValueError(_CONFIGURED_ERROR)
            continue
        if seen or len(fields) != 2 or re.fullmatch(r"[0-9]+", fields[1]) is None:
            raise ValueError(_CONFIGURED_ERROR)
        seen = True
        digits = fields[1].lstrip("0")
        if not digits or len(digits) > len(str(MAX_CONTEXT)):
            raise ValueError(_CONFIGURED_ERROR)
        result = int(digits)
        if not 1 <= result <= MAX_CONTEXT:
            raise ValueError(_CONFIGURED_ERROR)
    return result


def loaded_context(loaded_models: Any, model: str) -> int | None:
    """Read /api/ps context only for one unambiguous exact model identity.

    A different alias or matching model digest is never a substitute. Missing
    context is unknown. A present invalid context value is a metadata failure.
    """
    if (not isinstance(loaded_models, list) or not isinstance(model, str)
            or not model or model != model.strip()):
        raise ValueError(_LOADED_ERROR)
    identities = set()
    result = None
    for row in loaded_models:
        if not isinstance(row, dict):
            raise ValueError(_LOADED_ERROR)
        names = [row[key] for key in ("name", "model") if key in row]
        if (not names or any(not isinstance(name, str) or not name or name != name.strip() for name in names)
                or len(set(names)) != 1):
            raise ValueError(_LOADED_ERROR)
        identity = names[0]
        if identity in identities:
            raise ValueError(_LOADED_ERROR)
        identities.add(identity)
        if identity != model or "context_length" not in row:
            continue
        context = row["context_length"]
        if type(context) is not int or not 1 <= context <= MAX_CONTEXT:
            raise ValueError(_LOADED_ERROR)
        result = context
    return result
