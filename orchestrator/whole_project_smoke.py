"""Bound smoke authorship to public behavior and a small current-source view."""

from pathlib import PurePosixPath
import re

from .store import digest
from .whole_project_selection import projection_details


LEGACY_INLINE_SMOKE_POLICY = "inline-smoke-tests-v1"
INLINE_SMOKE_POLICY = "inline-smoke-tests-v2"
SMOKE_REFERENCE_REPAIR_STAGES = frozenset({"whole_self_reference_repair_1", "whole_self_reference_repair_2"})
SMOKE_STAGES = frozenset({"whole_self_tests", "whole_self_tests_retry"}) | SMOKE_REFERENCE_REPAIR_STAGES
SMOKE_OUTPUT_TOKENS = 32768
SMOKE_SOURCE_BYTES = 24576
SMOKE_GENERATION_POLICY = {
    "policy": "bounded-smoke-author-v1", "thinking_token_budget": 8192,
    "max_completion_tokens": SMOKE_OUTPUT_TOKENS,
}
SMOKE_REQUEST_GUIDANCE = (
    " For HTTP checks, bind each call to its actual request-helper signature. Use explicit keyword arguments "
    "for optional payload and credentials when supported. Keep the bearer token in the Authorization header, "
    "including on authenticated requests with no body. If an authenticated success path returns 401 or 403, "
    "inspect the failing call's argument binding and header construction before changing the login fixture or "
    "the expected status."
)
_ROUTE = re.compile(
    r"@(?:RestController|Controller|RequestMapping|GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping)\b"
    r"|\b(?:app|router|route|server)\.(?:get|post|put|patch|delete|route|use)\s*\("
    r"|\b(?:MapGet|MapPost|MapPut|MapDelete|APIRouter|urlpatterns)\b"
)


def public_smoke_plan(plan, *, policy=INLINE_SMOKE_POLICY):
    """Keep every public criterion and assumption, without implementation prose."""
    if policy not in {LEGACY_INLINE_SMOKE_POLICY, INLINE_SMOKE_POLICY}:
        raise ValueError("Unknown smoke context projection policy")
    projected = {key: value for key, value in plan.items()
                 if key in {"goal", "acceptance_criteria", "assumptions"}}
    return projected, {"policy": policy, "plan_digest": digest(plan),
                       "omitted_fields": sorted(plan.keys() - projected.keys())}


def smoke_author_data(files, *, byte_limit=SMOKE_SOURCE_BYTES, policy=INLINE_SMOKE_POLICY):
    """Include actual route declarations and their direct data types first.

    This is advisory source context, not a derived API contract. The original
    public brief remains authoritative. No file is invented and every omission
    or partial file is explicit; the full implementation inventory is retained.
    """
    if policy not in {LEGACY_INLINE_SMOKE_POLICY, INLINE_SMOKE_POLICY}:
        raise ValueError("Unknown smoke context projection policy")
    files = list(files)
    routes = {file.path for file in files if _ROUTE.search(file.content)}
    declarations = "\n".join(file.content for file in files if file.path in routes)
    dependencies = set()
    for file in files:
        symbol = PurePosixPath(file.path).name.split(".", 1)[0]
        if (file.path not in routes and len(symbol) >= 3
                and re.search(r"(?<![\w])" + re.escape(symbol) + r"(?![\w])", declarations)):
            dependencies.add(file.path)
    order = sorted(files, key=lambda file: (
        0 if file.path in routes else 1 if file.path in dependencies else 2, file.path))
    source, remaining = [], byte_limit
    for file in order:
        content = file.content.encode("utf-8")
        if len(content) <= remaining:
            source.append({"path": file.path, "content": file.content})
            remaining -= len(content)
        elif not source and remaining > 0:
            source.append({"path": file.path, "content": content[:remaining].decode("utf-8", "ignore"),
                           "truncated": True})
            remaining = 0
    tree = [{"path": file.path, "utf8_bytes": len(file.content.encode("utf-8")),
             "content_digest": digest(file.content)} for file in files]
    return {"implementation_files": [file.path for file in files], "implementation_tree": tree,
            "current_source": source,
            "source_projection": projection_details(tree, source, policy=policy)}


def project_smoke_inputs(inputs, stage):
    """Keep source binding in a digest, with the complete path inventory once.

    Reference test repair sees only the public contract, tests and diagnostics.
    It receives neither candidate source nor the trusted reference source.
    """
    if stage not in SMOKE_STAGES:
        raise ValueError("Smoke context projection requires an approved stage")
    projected = dict(inputs)
    if stage in SMOKE_REFERENCE_REPAIR_STAGES:
        if any(key in inputs for key in ("implementation_tree", "current_source", "source_projection")):
            raise ValueError("Reference smoke repair must not receive candidate source context")
        projected["source_projection"] = {
            "policy": INLINE_SMOKE_POLICY, "kind": "reference-test-repair-no-candidate-source",
            "candidate_source_included": False,
        }
    else:
        tree = projected.pop("implementation_tree")
        if [item["path"] for item in tree] != projected["implementation_files"]:
            raise ValueError("Smoke source inventory differs from the approved implementation paths")
        projected["implementation_inventory"] = {
            "policy": "path-inventory-digest-v1", "digest": digest(tree), "file_count": len(tree),
        }
    return projected
