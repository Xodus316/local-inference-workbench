"""Static coverage receipts, not a proof that generated assertions are correct."""

from __future__ import annotations

import ast
from collections import Counter

REFERENCE_POLICY = "file-local-test-reference-v2"


class TestSyntaxError(ValueError):
    """Repairable parser diagnostics, distinct from missing coverage references."""

    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(
            "Generated Python test file has invalid syntax: "
            + ", ".join(item["path"] for item in diagnostics)
        )


class MissingCoverageError(ValueError):
    """Only omitted criterion indexes authorize additive coverage repair."""

    def __init__(self, missing):
        self.missing = sorted(missing)
        super().__init__(
            "Test coverage must reference every acceptance criterion exactly by index; "
            f"missing={self.missing}, unknown=[]"
        )


def parse_test_files(files):
    trees, diagnostics = {}, []
    for path, content in files.items():
        if not path.endswith(".py"):
            continue
        try:
            trees[path] = ast.parse(content)
            # Compilation catches syntax that AST construction alone permits,
            # such as return/break outside a function/loop. It executes no code.
            compile(trees[path], path, "exec")
        except SyntaxError as error:
            diagnostics.append({"path": path, "line": error.lineno,
                                "column": error.offset, "message": error.msg})
    if diagnostics:
        raise TestSyntaxError(diagnostics)
    return trees


def python_test_names(files):
    """Inventory exact references without importing or executing generated code."""
    python_names = {}
    for path, tree in parse_test_files(files).items():
        names = set()
        for node in tree.body:
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.startswith("test_"):
                names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                names.update(
                    f"{node.name}.{method.name}"
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and method.name.startswith("test_")
                )
        python_names[path] = names
    return python_names


def correct_test_references(draft, *, qualify_classes=False):
    """Correct exact file-local references without modifying executable code.

    Call on new additions only: frozen coverage and executable files stay intact.
    V2 optionally qualifies bare names having exactly one class-method match.
    Misspellings, different files/classes and absent tests are never inferred.
    """
    files = {item.path: item.content for item in draft.files}
    names = python_test_names(files)
    counts = {}
    if qualify_classes:
        for path, tree in parse_test_files(files).items():
            counts[path] = Counter(
                f"{node.name}.{method.name}"
                for node in tree.body if isinstance(node, ast.ClassDef)
                for method in node.body
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                and method.name.startswith("test_"))
    value = draft.model_dump()
    corrections = []
    for index, entry in enumerate(value["coverage"]):
        original = entry["test_name"]
        available = names.get(entry["test_file"], set())
        if original in available:
            continue
        corrected = original.rstrip('{}[],:" \t\r\n')
        if qualify_classes and corrected not in available and corrected.isidentifier() and corrected.startswith("test_"):
            matches = [name for name in available if name.endswith("." + corrected)]
            if len(matches) == 1 and counts[entry["test_file"]][matches[0]] == 1:
                corrected = matches[0]
        if corrected != original and corrected in available:
            entry["test_name"] = corrected
            corrections.append({"policy": REFERENCE_POLICY if qualify_classes else "exact-test-reference-v1", "coverage_index": index,
                                "test_file": entry["test_file"], "original": original,
                                "corrected": corrected})
    return type(draft).model_validate(value), corrections


def validate_coverage_references(suite):
    """Check an addition's references without requiring every plan criterion yet."""
    files = {item.path: item.content for item in suite.files}
    python_names = python_test_names(files)
    for entry in suite.coverage:
        if entry.test_file not in files:
            raise ValueError(
                "Test coverage references a file absent from the test bundle"
            )
        if entry.test_file in python_names:
            if entry.test_name not in python_names[entry.test_file]:
                raise ValueError(
                    "Test coverage references a missing Python test function or Class.test_method"
                )
        elif entry.test_name not in files[entry.test_file]:
            raise ValueError(
                "Test coverage references a name absent from its test file"
            )


def validate_test_coverage(suite, criteria):
    expected = set(range(1, len(criteria) + 1))
    covered = {entry.criterion for entry in suite.coverage}
    if expected - covered and not covered - expected:
        raise MissingCoverageError(expected - covered)
    if covered != expected:
        raise ValueError(
            f"Test coverage must reference every acceptance criterion exactly by index; "
            f"missing={sorted(expected - covered)}, unknown={sorted(covered - expected)}"
        )
    validate_coverage_references(suite)
    return {
        "criteria": len(expected),
        "cases": len(suite.coverage),
        "references_checked": True,
        "semantic_correctness_verified": False,
    }
