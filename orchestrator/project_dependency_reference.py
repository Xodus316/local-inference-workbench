"""Validate explicitly approved public dependency signatures for source repairs."""

from copy import deepcopy
import re

from .store import digest


POLICY = "pinned-dependency-api-v1"
MAX_DOCUMENTS = 32
MAX_CONTENT_BYTES = 32 * 1024


def validate_dependency_reference(value, image_id):
    """Bind public API text to the exact verifier image without reading any source.

    This catalog is supplied explicitly by the run owner. The harness reference
    implementation is a separate input and is never consulted or projected here.
    """
    if value is None:
        return None
    if (not isinstance(value, dict) or value.keys() != {"policy", "image_id", "documents", "digest"}
            or value["policy"] != POLICY or not isinstance(image_id, str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None or value["image_id"] != image_id):
        raise ValueError("Dependency API reference must match the approved policy and pinned verifier image")
    documents = value["documents"]
    if not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise ValueError("Dependency API reference requires one to 32 public API documents")
    names, content_bytes = set(), 0
    for document in documents:
        if (not isinstance(document, dict) or document.keys() != {"name", "content"}
                or not isinstance(document["name"], str) or not 1 <= len(document["name"]) <= 240
                or not document["name"].strip() or any(not char.isprintable() for char in document["name"])
                or document["name"] in names or not isinstance(document["content"], str)
                or not document["content"].strip() or "\0" in document["content"]):
            raise ValueError("Dependency API reference has an invalid or duplicate public API document")
        names.add(document["name"])
        content_bytes += len(document["content"].encode("utf-8"))
    if content_bytes > MAX_CONTENT_BYTES or value["digest"] != digest(documents):
        raise ValueError("Dependency API reference exceeds its source bound or its approved digest changed")
    return deepcopy(value)
