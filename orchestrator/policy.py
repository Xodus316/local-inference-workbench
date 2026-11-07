from pathlib import PurePosixPath, PureWindowsPath
from .schemas import Assessment, CodePlan

def safe_relative(path: str):
    normalized = path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    protected = {
        ".git",
        ".ssh",
        ".aws",
        ".codex",
        ".agents",
        "node_modules",
        "__pycache__",
    }
    if (
        not parts
        or PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(path).drive
        or any(
            p in {".", ".."} or p.lower() in protected or p.lower().startswith(".env")
            for p in parts
        )
        or "\x00" in path
        or ":" in normalized
        or any(p.lower().endswith((".pem", ".key", ".p12", ".pfx")) for p in parts)
        or any(
            p.lower() in {"credentials", "id_rsa", "id_ed25519", "api-token.txt"}
            for p in parts
        )
    ):
        raise ValueError("Protected or escaping repository path")
    return normalized


def citation_checks(result: Assessment, sources):
    by_id = {s["id"]: s for s in sources}
    checks = []
    for cite in result.evidence:
        source = by_id.get(cite.source_id)
        lines = source["text"].splitlines() if source else []
        matches = (
            1 <= cite.start_line <= cite.end_line <= len(lines)
            and "\n".join(lines[cite.start_line - 1 : cite.end_line]) == cite.quote
        )
        checks.append(
            {
                "source_id": cite.source_id,
                "start_line": cite.start_line,
                "end_line": cite.end_line,
                "identity_matches": matches,
            }
        )
    return {
        "citations": checks,
        "all_citations_match": all(c["identity_matches"] for c in checks),
        "citation_count": len(checks),
        "semantic_truth_verified": False,
    }


def validate_plan(plan: CodePlan, sources):
    source_ids = {s["id"] for s in sources}
    evidence_ids = source_ids | {
        f'{source["id"]}:L{number}'
        for source in sources
        for number, line in enumerate(source.get("text", "").splitlines(), 1)
        if line
    }
    steps = {s.id: s for s in plan.steps}
    if len(steps) != len(plan.steps):
        raise ValueError("Duplicate plan step IDs")
    for step in plan.steps:
        for path in step.files:
            safe_relative(path)
        if not set(step.evidence_ids) <= evidence_ids:
            raise ValueError("Plan cites unknown evidence")
        if not set(step.depends_on) <= steps.keys() or len(set(step.depends_on)) != len(
            step.depends_on
        ):
            raise ValueError("Invalid plan dependencies")
    depths = {}

    def visit(key, active):
        if key in active:
            raise ValueError("Cyclic plan")
        if key in depths:
            return depths[key]
        depth = 1 + max(
            (visit(d, active | {key}) for d in steps[key].depends_on), default=0
        )
        if depth > 6:
            raise ValueError("Plan exceeds maximum dependency depth")
        depths[key] = depth
        return depth

    for key in steps:
        visit(key, set())
    return plan.model_dump()
