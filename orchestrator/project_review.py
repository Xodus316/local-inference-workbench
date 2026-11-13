"""Bound and ground independent reviews of assembled offline projects."""
from collections.abc import Callable, Sequence
from pathlib import PurePosixPath
import re
from typing import Literal

from pydantic import Field, model_validator

from .build_models import BuildFile
from .schemas import Record


REVIEW_BRIEF = (
    "Review the supplied implementation files against the original brief and approved interfaces. "
    "Look for concrete defects in correctness, data integrity and concurrent requests, authentication "
    "and authorization, input validation, integration, usability and keyboard access, and significant "
    "test or maintainability gaps. Check that dependent files agree about APIs and state when their "
    "source is available. Report a concrete failure scenario or maintenance impact for each finding. "
    "Use P0 when the application is unusable or system security is compromised, P1 for serious defects "
    "such as data loss or broken core flows, P2 for significant gaps with limited impact, and P3 for "
    "localized issues. For P0 or P1, give a concrete input or sequence the application can receive, "
    "the behavior required by the brief or contract, and the incorrect result that follows from the "
    "cited code. For numeric defects, trace the actual operations, types, and rounding in order and "
    "show an input for which the actual result differs from the expected result. For a concurrency "
    "defect, describe the request interleaving and the resulting state. Treat unmeasured performance "
    "and maintainability concerns as P2 or P3 unless the code shows a concrete failure of required "
    "behavior. Avoid speculative findings and cosmetic preferences. Only cite files in this "
    "review chunk. For every finding, use the displayed line number and copy a verbatim excerpt from "
    "that single line after its ' | ' prefix, then explain the impact and describe a concrete fix. "
    "Before submitting, check the excerpt on the cited line. Identify behaviors that "
    "warrant testing without assuming what the independent suite covers. Never invent results from "
    "tests or a browser. Do not change source or tests. Return findings in priority order, "
    "using an empty list if you found no actionable defects in the supplied files."
)


class CodeReviewFinding(Record):
    severity: Literal["P0", "P1", "P2", "P3"]
    category: Literal["correctness", "data_integrity", "security", "integration", "usability",
                      "testing", "maintainability"]
    path: str = Field(min_length=1, max_length=500)
    line: int = Field(ge=1, description="One-based source line in the supplied file.")
    evidence: str = Field(min_length=1, max_length=500,
                          description="Verbatim excerpt of the cited line, without ellipses.")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=900)
    fix: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def meaningful_text(self):
        if any(not value.strip() for value in (self.evidence, self.title, self.description, self.fix)):
            raise ValueError("Review findings require evidence, title, impact and a concrete fix")
        return self


class ProjectCodeReview(Record):
    findings: list[CodeReviewFinding] = Field(max_length=12)


class ReviewCitationError(ValueError):
    """A finding does not identify a source excerpt in its supplied chunk."""

    def __init__(self, message: str, *, finding_index: int, validated_findings: list[CodeReviewFinding]):
        super().__init__(message)
        self.finding_index = finding_index
        self.validated_findings = validated_findings


SOURCE_SUFFIXES = {".java", ".vue", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"}
GENERATED_DIRECTORIES = {"build", "dist", "generated", "node_modules", "vendor"}
DISPLAY_LINE_PREFIX = re.compile(r"^(?:[1-9][0-9]*)? \| ")


def _source_lines(item: BuildFile) -> list[str]:
    return item.content.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def numbered_review_source(files: Sequence[BuildFile]) -> list[dict[str, str]]:
    """Give the reviewer the same line numbers the evidence validator uses."""
    return [{"path": item.path, "numbered_content": "\n".join(
        f"{number} | {line}" for number, line in enumerate(_source_lines(item), 1)
    )} for item in files]


def style_findings(files: Sequence[BuildFile]) -> list[CodeReviewFinding]:
    """Report substantial code compression without imposing a formatter or line-width rule."""
    findings = []
    for item in files:
        path = PurePosixPath(item.path.lower())
        if (path.suffix not in SOURCE_SUFFIXES or GENERATED_DIRECTORIES.intersection(path.parts[:-1])
                or any(part in {"min", "generated"} for part in path.name.split(".")[1:-1])):
            continue
        lines = _source_lines(item)
        header = "\n".join(lines[:5]).lower()
        if "generated" in header and "do not edit" in header:
            continue
        for number, line in enumerate(lines, 1):
            code = line.strip()
            if (len(code) <= 500 or code.startswith(("#", "//", "/*", "*", "<!--"))
                    or sum(code.count(token) for token in (";", "{", "}", "</")) < 12):
                continue
            findings.append(CodeReviewFinding(
                severity="P2" if len(code) > 1000 else "P3", category="maintainability",
                path=item.path, line=number, evidence=code[:120], title="Heavily compressed source",
                description=f"This {len(code)}-character line combines many statements or elements, "
                            "making individual changes and defects difficult to review.",
                fix="Put statements and markup on separate lines with indentation that follows the project style.",
            ))
            break
    return findings


def review_chunks(files: Sequence[BuildFile], fits: Callable[[list[BuildFile]], bool]) -> list[list[BuildFile]]:
    """Pack complete files in source order using the caller's full prompt budget."""
    chunks = []
    current = []
    for item in files:
        candidate = [*current, item]
        if fits(candidate):
            current = candidate
            continue
        if not current or not fits([item]):
            raise ValueError(f"Complete review source for {item.path!r} exceeds the frozen reviewer context")
        chunks.append(current)
        current = [item]
    if current:
        chunks.append(current)
    return chunks


def validate_review(value: object, files: Sequence[BuildFile]) -> ProjectCodeReview:
    """Accept findings only when their evidence identifies the exact reviewed source."""
    review, _ = _check_citations(value, files, recover=False)
    return review


def resolve_review_citations(
    value: object, files: Sequence[BuildFile],
) -> tuple[ProjectCodeReview, list[dict[str, int | str | bool]]]:
    """Remove copied display prefixes and relocate unambiguous quotes within the cited file."""
    return _check_citations(value, files, recover=True)


def _check_citations(
    value: object, files: Sequence[BuildFile], *, recover: bool,
) -> tuple[ProjectCodeReview, list[dict[str, int | str | bool]]]:
    review = ProjectCodeReview.model_validate(value)
    lines = {item.path: _source_lines(item) for item in files}
    findings = []
    corrections = []
    failure = None
    for index, finding in enumerate(review.findings):
        source = lines.get(finding.path)
        if source is None:
            error = f"Review finding cites a file outside its supplied source: {finding.path!r}"
        elif finding.line <= len(source) and finding.evidence in source[finding.line - 1]:
            findings.append(finding)
            continue
        elif recover and (resolved := _resolve_quote(finding, source)) is not None:
            line, evidence = resolved
            findings.append(finding.model_copy(update={"line": line, "evidence": evidence}))
            corrections.append({"finding_index": index, "path": finding.path, "original_line": finding.line,
                                "line": line, "display_prefix_removed": evidence != finding.evidence})
            continue
        else:
            error = f"Review finding evidence does not match {finding.path!r} at line {finding.line}"
        if failure is None:
            failure = (index, error)
    if failure is not None:
        index, message = failure
        raise ReviewCitationError(message, finding_index=index, validated_findings=findings)
    if corrections:
        review = review.model_copy(update={"findings": findings})
    return review, corrections


def _resolve_quote(finding: CodeReviewFinding, source: Sequence[str]) -> tuple[int, str] | None:
    raw = finding.evidence
    stripped = DISPLAY_LINE_PREFIX.sub("", raw, count=1)
    if not stripped.strip():
        return None
    if finding.line <= len(source) and stripped in source[finding.line - 1]:
        return finding.line, stripped
    for evidence in (raw,) if raw == stripped else (raw, stripped):
        matching = [number for number, line in enumerate(source, 1) if evidence in line]
        if len(matching) == 1:
            return matching[0], evidence
        if matching:
            break
    return None
