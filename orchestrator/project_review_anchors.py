"""Exact source addresses for native reviews; finding prose is never rewritten."""

from copy import deepcopy
import hashlib

from pydantic import Field, ValidationError, create_model

from .project_review import CodeReviewFinding, validate_review
from .schemas import Record
from .store import digest
from .wire import wire_schema


REGISTRY_POLICY = "exact-source-review-anchors-v1"
EXPANSION_POLICY = "native-review-anchor-expansion-v1"
PROSE_FIELDS = ("severity", "category", "title", "description", "fix")
SEGMENT_CHARACTERS = 500


class SourceAnchorError(ValueError):
    def __init__(self, message, *, finding_index, validated_findings):
        super().__init__(message)
        self.finding_index = finding_index
        self.validated_findings = validated_findings


def _ranges(numbers):
    result = []
    for number in numbers:
        if result and result[-1][1] + 1 == number:
            result[-1][1] = number
        else:
            result.append([number, number])
    return result


def source_anchor_registry(files):
    """Address every nonblank window without shortening the displayed source.

    Line endings follow the canonical citation validator. Hashes still bind the
    original file bytes. Segment offsets are Unicode characters, not bytes.
    """
    source = [item.model_dump() for item in files]
    if len({item["path"] for item in source}) != len(source):
        raise ValueError("Duplicate source path")
    anchors, metadata, prefixes = {}, [], set()
    for item in source:
        path, content = item["path"], item["content"]
        prefix = "s" + digest({"path": path, "content": content})[:12]
        if prefix in prefixes:
            raise ValueError("Source anchor prefix collision")
        prefixes.add(prefix)
        eligible, segmented = [], {}
        lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            eligible.append(number)
            segments = []
            for offset in range(0, len(line), SEGMENT_CHARACTERS):
                excerpt = line[offset:offset + SEGMENT_CHARACTERS]
                if not excerpt.strip():
                    continue
                segment = offset // SEGMENT_CHARACTERS + 1
                anchor = f"{prefix}:L{number}"
                if len(line) > SEGMENT_CHARACTERS:
                    anchor += f":S{segment}"
                anchors[anchor] = {
                    "path": path, "line": number, "evidence": excerpt,
                    "start_character": offset, "end_character": offset + len(excerpt),
                }
                segments.append(segment)
            if len(line) > SEGMENT_CHARACTERS:
                segmented[str(number)] = segments
        metadata.append({
            "path": path, "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "anchor_prefix": prefix, "eligible_line_ranges": _ranges(eligible),
            "segmented_lines": segmented,
        })
    return {"policy": REGISTRY_POLICY, "source_digest": digest(source), "files": metadata, "anchors": anchors}


def source_anchor_inputs(registry):
    return {
        "policy": REGISTRY_POLICY, "registry_digest": digest(registry),
        "source_digest": registry["source_digest"], "files": deepcopy(registry["files"]),
        "address_format": "<anchor_prefix>:L<one-based line>; append :S<segment> only for segmented_lines",
        "segment_characters": SEGMENT_CHARACTERS, "all_source_remains_in_implementation": True,
    }


def source_anchor_review_type(registry):
    fields = {name: (CodeReviewFinding.model_fields[name].annotation,
                     deepcopy(CodeReviewFinding.model_fields[name])) for name in PROSE_FIELDS}
    ids = list(registry["anchors"])
    # The wire enum constrains decoding; expansion independently checks membership.
    fields["anchor_id"] = (str, Field(
        min_length=1, max_length=80,
        description="Select one exact source address from this enum; do not emit code or coordinates.",
        json_schema_extra={"enum": ids or ["__no_source_anchors__"]},
    ))
    finding = create_model("SourceAnchoredReviewFinding", __base__=Record, **fields)
    return create_model("SourceAnchoredProjectReview", __base__=Record,
                        findings=(list[finding], Field(max_length=12 if ids else 0)))


def source_anchor_contract_view(registry):
    """Include the complete anchor enum once, in the counted response schema."""
    schema = wire_schema(source_anchor_review_type(registry))
    original = digest(schema)
    entry = schema["properties"]["findings"]["items"]["properties"]["anchor_id"]
    ids = entry.pop("enum")
    return {
        "policy": "native-review-anchor-enum-once-v1", "native_schema_digest": original,
        "contract_without_duplicate_anchor_enum": schema,
        "anchor_enum": {"location": "native response schema", "registry_digest": digest(registry),
                        "count": len(ids), "digest": digest(ids)},
    }


def _expand(finding, registry):
    original = finding.model_dump()
    address = registry["anchors"][original["anchor_id"]]
    return CodeReviewFinding.model_validate({
        **{name: original[name] for name in PROSE_FIELDS},
        **{name: address[name] for name in ("path", "line", "evidence")},
    })


def grounded_review_findings(value, registry, files):
    """Retain independently valid rows even when another row violates the schema.

    This grants no acceptance to the invalid response. It only prevents a later
    correction from silently erasing an already grounded finding.
    """
    if registry != source_anchor_registry(files):
        raise ValueError("Source anchor registry is stale or altered")
    if not isinstance(value, dict) or set(value) != {"findings"}:
        return []
    rows = value["findings"]
    if not isinstance(rows, list) or len(rows) > 12:
        return []
    model = source_anchor_review_type(registry)
    grounded = []
    for row in rows:
        try:
            finding = model.model_validate({"findings": [row]}).findings[0]
            expanded = _expand(finding, registry)
            validate_review({"findings": [expanded]}, files)
        except (ValidationError, KeyError, IndexError):
            continue
        grounded.append(expanded)
    return grounded


def expand_source_anchor_review(value, registry, files):
    """Expand coordinates only, then apply strict canonical citation validation."""
    if registry != source_anchor_registry(files):
        raise ValueError("Source anchor registry is stale or altered")
    native = source_anchor_review_type(registry).model_validate(value)
    canonical, mappings = [], []
    for index, finding in enumerate(native.findings):
        anchor = finding.anchor_id
        if anchor not in registry["anchors"]:
            raise SourceAnchorError(
                f"Unregistered source anchor {anchor!r}", finding_index=index,
                validated_findings=grounded_review_findings(value, registry, files),
            )
        row = _expand(finding, registry)
        canonical.append(row.model_dump())
        mappings.append({"finding_index": index, "anchor_id": anchor,
                         "address_digest": digest(registry["anchors"][anchor]),
                         "canonical_finding_digest": digest(row.model_dump())})
    review = validate_review({"findings": canonical}, files)
    return review, {
        "policy": EXPANSION_POLICY, "native_value_digest": digest(value),
        "registry_digest": digest(registry), "source_digest": registry["source_digest"],
        "canonical_review_digest": digest(review.model_dump()), "mappings": mappings,
        "canonical_is_literal_native_output": False, "prose_and_order_unchanged": True,
    }
