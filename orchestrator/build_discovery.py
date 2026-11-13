"""Match independently authored coverage references to verbose unittest receipts."""
import re
from collections import Counter
from pathlib import PurePosixPath

from .build_models import BuildTestCoverage
from .build_strength import COMMANDS


# Newer unittest versions include the method inside the parentheses. Older
# versions print only module.Class there. A docstring moves "... ok" to line two.
CASE_LINE = re.compile(r"(?P<method>test_\w*) \((?P<qualname>[\w.]+)\)(?P<status> \.\.\. .*)?")
COUNT_LINE = re.compile(r"(?m)^Ran (\d+) tests? in ")
SUFFIXED_TEST_PATH = re.compile(r"tests/([A-Za-z_][A-Za-z0-9_]*)_test\.py")
ENTRY_POINT_GUARD = re.compile(r"(?m)^[ \t]*if\s+__name__\s*==\s*(['\"])__main__\1\s*:")
DYNAMIC_MODULE_LOOKUP = re.compile(r"\b(?:__import__|importlib|runpy|pkgutil|sys\s*\.\s*modules|exec|eval)\b")
MODULE_IDENTITY = re.compile(r"\b(?:__name__|__file__|__spec__|__loader__|__package__)\b")
FUNCTION_NAME = re.compile(r"(?m)^([ \t]*(?:async[ \t]+)?def[ \t]+)\w+(?=[ \t]*\()")


def normalize_authored_test_paths(value):
    """Correct the suffix form of top-level unittest filenames before freezing the suite."""
    renames = {}
    for file in value["files"]:
        path = file["path"]
        match = SUFFIXED_TEST_PATH.fullmatch(path)
        if match and not path.startswith("tests/test_"):
            renames[path] = f"tests/test_{match[1]}.py"
    if not renames:
        return value, []

    occupied = {file["path"].casefold() for file in value["files"]}
    occupied.update(path.casefold() for path in value["implementation_files"])
    targets = {path.casefold() for path in renames.values()}
    if len(targets) != len(renames) or targets & occupied:
        raise ValueError("Test filename correction conflicts with an existing path")

    entries = [BuildTestCoverage.model_validate(entry) for entry in value["coverage"]]
    references = {entry.test_file for entry in entries}
    if renames.keys() - references or targets & {path.casefold() for path in references}:
        raise ValueError("Test filename correction requires unambiguous references to the authored paths")

    modules = {PurePosixPath(path).stem for path in (*renames, *renames.values())}
    module_reference = re.compile(r"\b(?:" + "|".join(sorted(modules)) + r")\b", re.IGNORECASE)
    # This conservative source scan also works before a syntax repair. Never rewrite code or imports.
    for file in value["files"]:
        path, source = file["path"], file["content"]
        if (module_reference.search(FUNCTION_NAME.sub(r"\1", source))
                or (path.endswith(".py") and DYNAMIC_MODULE_LOOKUP.search(source))
                or (path in renames and MODULE_IDENTITY.search(ENTRY_POINT_GUARD.sub("", source)))):
            raise ValueError("Test filename correction cannot change paths referenced by test source")

    return {
        **value,
        "files": [{**file, "path": renames.get(file["path"], file["path"])} for file in value["files"]],
        "coverage": [{**entry, "test_file": renames.get(entry["test_file"], entry["test_file"])}
                     for entry in value["coverage"]],
    }, [{"from": source, "to": target} for source, target in sorted(renames.items())]


def _referenced_cases(suite):
    cases = set()
    for entry in suite.coverage:
        path = PurePosixPath(entry.test_file)
        names = entry.test_name.split(".")
        module = path.with_suffix("").parts[1:]
        if (path.parts[:1] != ("tests",) or path.suffix != ".py" or not path.name.startswith("test_")
                or not all(part.isidentifier() for part in module)
                or len(names) != 2 or not all(name.isidentifier() for name in names)
                or not names[1].startswith("test_")):
            raise ValueError("Coverage requires discoverable Python files and Class.test_method references")
        cases.add(".".join((*module, *names)))
    if not cases:
        raise ValueError("No independent test cases were referenced")
    return cases


def validate_discovery_suite(suite):
    if len(suite.commands) != 1 or suite.commands[0] not in COMMANDS:
        raise ValueError("Build requires python3 -m unittest discover -s tests -v or its python equivalent")
    _referenced_cases(suite)


def covered_cases_ran(receipt, suite):
    """Require one explicit successful execution of each referenced test in this command."""
    rows = receipt.get("commands") or []
    if len(rows) != 1 or len(suite.commands) != 1 or suite.commands[0] not in COMMANDS:
        return False
    row = rows[0]
    if (receipt.get("status") != "passed" or row.get("cleanup_confirmed") is not True
            or row.get("cancelled") or row.get("timed_out") or row.get("exit_code") != 0):
        return False
    try:
        expected = _referenced_cases(suite)
    except ValueError:
        return False
    log = row.get("stderr") or ""
    counts = COUNT_LINE.findall(log)
    if len(counts) != 1:
        return False
    lines = log.splitlines()
    observed = Counter()
    passed = set()
    for index, line in enumerate(lines):
        case = CASE_LINE.fullmatch(line)
        if not case:
            continue
        method = case["method"]
        name = case["qualname"]
        test_id = name if name.endswith("." + method) else f"{name}.{method}"
        observed[test_id] += 1
        status = case["status"]
        if status is None and index + 1 < len(lines) and not CASE_LINE.fullmatch(lines[index + 1]):
            status = " ... ok" if lines[index + 1].endswith(" ... ok") else None
        if status == " ... ok":
            passed.add(test_id)
    return (sum(observed.values()) == int(counts[0]) and expected <= passed
            and all(observed[name] == 1 for name in expected))
