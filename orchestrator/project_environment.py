"""Operator-authored, portable offline image capabilities and static compatibility checks."""
import ast
import json
import re
import sys
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from .build_coverage import parse_test_files
from .repository import relative
from .schemas import Record

POLICY = "offline-project-environment-v1"
Name = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,79}$")]
Module = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$", max_length=120)]
PythonVersion = Annotated[str, Field(pattern=r"^3\.(?:[7-9]|[1-9][0-9])$")]
CheckName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class ProjectTool(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}$")
    path: str = Field(min_length=2, max_length=240)

    @field_validator("path")
    @classmethod
    def container_path(cls, value):
        if (not value.startswith("/") or value.startswith("//") or "\\" in value
                or str(PurePosixPath(value)) != value or ".." in value.split("/")
                or any(ord(c) < 32 for c in value)):
            raise ValueError("Tool paths must be canonical absolute container POSIX paths")
        return value


class ProjectAPI(Record):
    base_url_env: Name
    base_url_kind: Literal["origin", "api_root"]
    path_prefix: str = Field(default="", max_length=160)

    @field_validator("path_prefix")
    @classmethod
    def prefix(cls, value):
        if not re.fullmatch(r"(?:/[A-Za-z0-9._~-]+)*", value) or any(p in {".", ".."} for p in value.split("/")):
            raise ValueError("Use a canonical API path prefix without a trailing slash")
        return value


class ProjectVerification(Record):
    policy: CheckName
    required_checks: list[CheckName] = Field(min_length=1, max_length=24)

    @field_validator("required_checks")
    @classmethod
    def distinct_checks(cls, checks):
        if len(checks) != len(set(checks)):
            raise ValueError("Required verification checks must be distinct")
        return checks


class ProjectCompileCheckpoint(Record):
    name: CheckName
    source_root: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    required_checks: list[CheckName] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid_component(self):
        if relative(self.source_root) != self.source_root or self.source_root.casefold() == "tests":
            raise ValueError("Compile checkpoints require a portable implementation directory")
        if len(self.required_checks) != len(set(self.required_checks)):
            raise ValueError("Required compile checks must be distinct")
        return self


class ProjectEnvironment(Record):
    """Metadata only: no arbitrary environment values, credentials or source fields."""
    policy: Literal["offline-project-environment-v1"] = POLICY
    required_application_files: list[str] = Field(default_factory=list, max_length=128)
    python_modules: list[Module] | None = Field(default=None, max_length=64)
    python_version: PythonVersion | None = None
    tools: list[ProjectTool] | None = Field(default=None, max_length=64)
    api: ProjectAPI | None = None
    frontend_http_server: bool | None = None
    frontend_base_url_env: Name | None = None
    verification: ProjectVerification | None = None
    compile_checkpoints: list[ProjectCompileCheckpoint] | None = Field(default=None, min_length=1, max_length=4)

    @model_serializer(mode="wrap")
    def serialize_environment(self, handler):
        value = handler(self)
        if self.python_version is None:
            value.pop("python_version", None)
        if self.verification is None:
            value.pop("verification", None)
        if self.compile_checkpoints is None:
            value.pop("compile_checkpoints", None)
        return value

    @model_validator(mode="after")
    def bounded_portable_metadata(self):
        paths = self.required_application_files
        if (any(len(p) > 240 or relative(p) != p or p.casefold().split("/")[0] == "tests" for p in paths)
                or len({p.casefold() for p in paths}) != len(paths)):
            raise ValueError("Required application paths must be distinct canonical implementation paths")
        if self.python_modules is not None and len(set(self.python_modules)) != len(self.python_modules):
            raise ValueError("Repeated Python module capability")
        if self.tools is not None and len({t.name for t in self.tools}) != len(self.tools):
            raise ValueError("Tool capability names must be distinct")
        if (self.api is not None and self.frontend_base_url_env == self.api.base_url_env):
            raise ValueError("API and frontend URL variables must have different meanings")
        if self.compile_checkpoints is not None:
            names = {item.name for item in self.compile_checkpoints}
            roots = {item.source_root.casefold() for item in self.compile_checkpoints}
            if (self.verification is None or len(names) != len(self.compile_checkpoints)
                    or len(roots) != len(self.compile_checkpoints)):
                raise ValueError("Compile checkpoints require image verification and distinct names and directories")
        if len(json.dumps(self.model_dump(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 8192:
            raise ValueError("Project environment metadata exceeds 8192 UTF-8 bytes")
        return self


def validate_project_environment(bundle):
    """Reject modified/noncanonical metadata; None preserves historical profile behavior."""
    configured = bundle.get("build", {}).get("project_environment")
    frozen = bundle.get("project_environment")
    if configured is None and frozen is None:
        return None
    canonical = ProjectEnvironment.model_validate(configured).model_dump()
    if configured != canonical or frozen != canonical:
        raise ValueError("Project environment must equal the canonical operator configuration frozen for approval")
    return canonical


def frontend_dom_test_instructions(environment):
    tools = {tool["name"]: tool["path"] for tool in (environment or {}).get("tools") or []}
    runner = tools.get("vitest-runner")
    if runner is None or "node" not in tools:
        return ""
    return (
        " Use the image's vitest-runner for frontend DOM tests. Each Python unittest must supply its own "
        "complete TypeScript spec in a raw triple-quoted string (or shared test helper). Leave TypeScript "
        "braces as written. Pass fixture values in the Python subprocess env and read process.env from "
        "TypeScript. Do not interpolate the spec with Python f-strings or .format(). "
        "From Python, call subprocess.run(['node', " + repr(runner) + "], input=spec_source, "
        "stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=45). "
        "Assert the result's returncode is 0 and include Vitest's failure summary in the assertion "
        "message. The runner provides jsdom, the app's Vite configuration and "
        "a temporary spec resolved from the frontend root. Use only 'vitest', '@vue/test-utils', "
        "Node built-ins and native DOM APIs for test utilities. Testing Library and Playwright are "
        "unavailable. Import application modules as './src/...'. "
        "Do not rely on candidate-owned tests, invoke raw Vitest, write to the application tree or supply "
        "your own Vite config. For route-level UI checks, import "
        "{ startApp, fillByLabel, submitForm, clickButton, find, waitFor } from '#workbench-dom'. "
        "Use one async Vitest test and call startApp once per spec with a dynamic import "
        "callback for the entry named in the project brief and an initial path, for example "
        "await startApp(() => import('./src/main.ts'), { path: '/login' }). It loads index.html's body "
        "and boots the app. Await fillByLabel(label, value) to set an actual input and dispatch a bubbling "
        "event; it matches the visible label ignoring case and a trailing required marker, so 'Name' "
        "finds 'Name *'. Await clickButton(text) to click a button by its visible text; it searches the "
        "open dialog first and accepts a single button whose text starts with those words. "
        "Await find(selector) to get a rendered element and waitFor(() => expect(...)) for asynchronous "
        "results. After a dialog save, wait for the saved values to appear in the list; the entered name "
        "can also match a dialog title. Do not invent exported factories, component paths or storage keys. "
        "Exercise real sign-in for protected pages when the API is available. After filling the "
        "credentials, call await submitForm() for a page with one form, or pass its selector if more "
        "than one exists. This clicks the form's actual submit control without assuming its caption. "
        "The Login and Register tabs select a mode. Submit the form after selecting a mode. Navigate via "
        "public links and use deterministic fixtures created through the real API when needed. Use separate "
        "unittest cases with their own specs for independent route behaviors where practical. Wait for "
        "asynchronous renders and assert observable behavior using DOM roles, labels, text or values. "
        "Require the control and verify the resulting change instead of skipping the action if it is missing."
    )


def frontend_dom_repair_instructions(environment):
    tools = {tool["name"] for tool in (environment or {}).get("tools") or []}
    if not {"node", "vitest-runner"} <= tools:
        return ""
    return (
        " For DOM repair, wait for sign-in and its redirect before selecting a navigation control. "
        "Wait for a rendered link to the specified public href or a semantic control for that action. "
        "Click it, then verify the target pathname and route content before interacting with the page. "
        "Use rendered links for SPA navigation. Verify an anonymous route guard in a fresh spec started "
        "at the protected public path. Locate actions by visible text, role, aria-label, title or an icon "
        "specified in the brief. Use native DOM selectors and require every control before clicking it. "
        "In jsdom, use textContent for rendered text and accept equivalent wording when the brief leaves "
        "captions unspecified. Assert actual fixture data in route content or the fixture's table row. "
        "For CRUD, exercise real create, edit and delete controls scoped to the relevant row or dialog. "
        "Open selectors and select the visible fixture option. After saving, wait for the dialog to close "
        "and verify the refreshed row. Confirm deletion through the dialog and wait for the row to disappear."
    )


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value) + "." + node.attr
    return ""


def _aliases(tree, module, members=()):
    result = {module + "." + member for member in members}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    result.update((alias.asname or module) + "." + member for member in members)
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            result.update(alias.asname or alias.name for alias in node.names if alias.name in members)
    return result


def _environment_read(node, tree):
    environ = _aliases(tree, "os", ["environ"])
    getenv = _aliases(tree, "os", ["getenv"])
    if isinstance(node, ast.Subscript) and _name(node.value) in environ:
        key = node.slice
    elif isinstance(node, ast.Call) and node.args and (
            _name(node.func) in getenv or isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and _name(node.func.value) in environ):
        key = node.args[0]
    else:
        return None
    return key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else None


def _import_issues(path, tree, environment, local_modules):
    modules = environment["python_modules"]
    if modules is None:
        return []
    available = {m.split(".")[0] for m in modules} | sys.stdlib_module_names | local_modules
    issues = []
    for node in ast.walk(tree):
        imported = []
        if isinstance(node, ast.Import):
            imported = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            imported = [node.module]
        for module in imported:
            if module.split(".")[0] not in available:
                issues.append({"path": path, "line": node.lineno, "kind": "unsupported_python_import",
                    "message": f"Python module {module} is absent from the image's declared module inventory"})
    return issues


def _tool_issues(path, tree, environment):
    if environment["tools"] is None:
        return []
    tools = {value for tool in environment["tools"] for value in (tool["name"], tool["path"])}
    tools.update({"python", "python3", "sh", "/bin/sh"})  # Required by the profile's frozen unittest command.
    calls = _aliases(tree, "subprocess", ["run", "Popen", "call", "check_call", "check_output"])
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _name(node.func) not in calls:
            continue
        argv = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "args"), None)
        if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
            continue
        executable = argv.elts[0]
        if isinstance(executable, ast.Constant) and isinstance(executable.value, str) and executable.value not in tools:
            issues.append({"path": path, "line": node.lineno, "kind": "unsupported_tool",
                "message": f"Executable {executable.value} is absent from the image's declared tool inventory"})
    return issues


def _api_issues(path, tree, api):
    if not api or api["base_url_kind"] != "api_root" or not api["path_prefix"]:
        return []
    roots = set()
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def owner(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.ClassDef):
                return node.name
        return None
    def base(node):
        if _environment_read(node, tree) == api["base_url_env"] or _name(node) in roots:
            return True
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"strip", "rstrip"} and base(node.func.value)
                and all(isinstance(a, ast.Constant) and a.value == "/" for a in node.args))
    assignments = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _name(target):
                    assignments.setdefault(_name(target), []).append(node.value)
    for _ in range(4):
        for name, values in assignments.items():
            # Ambiguous/reassigned names are not evidence for a hard rejection.
            if all(base(value) for value in values):
                roots.add(name)
    prefix = api["path_prefix"]
    duplicated = lambda value: isinstance(value, str) and (value == prefix or value.startswith(prefix + "/"))
    def suffixes(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and base(node.left):
            return [node.right]
        if isinstance(node, ast.JoinedStr):
            return [b for a, b in zip(node.values, node.values[1:])
                    if isinstance(a, ast.FormattedValue) and base(a.value)]
        return []
    issues, helpers = [], {}
    for node in ast.walk(tree):
        for suffix in suffixes(node):
            if isinstance(suffix, ast.Constant) and duplicated(suffix.value):
                issues.append({"path": path, "line": node.lineno, "kind": "duplicated_api_prefix",
                    "message": f"{api['base_url_env']} already includes {prefix}; append API-relative endpoint paths"})
        if isinstance(node, ast.FunctionDef):
            args = [a.arg for a in node.args.args]
            for child in ast.walk(node):
                for suffix in suffixes(child):
                    if isinstance(suffix, ast.Name) and suffix.id in args:
                        helpers[(owner(node), node.name)] = (
                            args.index(suffix.id) - (bool(args) and args[0] in {"self", "cls"}), suffix.id)
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = _name(call.func)
        key = (owner(call), name.split(".", 1)[1]) if name.startswith(("self.", "cls.")) else (None, name)
        if key not in helpers:
            continue
        index, argument = helpers[key]
        value = call.args[index] if 0 <= index < len(call.args) else next(
            (k.value for k in call.keywords if k.arg == argument), None)
        if isinstance(value, ast.Constant) and duplicated(value.value):
            issues.append({"path": path, "line": call.lineno, "kind": "duplicated_api_prefix",
                "message": f"{api['base_url_env']} already includes {prefix}; the request helper adds this path twice"})
    return issues


def suite_environment_issues(suite, environment):
    """High-confidence static conflicts only; supplied tests are never imported or executed."""
    if environment is None:
        return []
    environment = ProjectEnvironment.model_validate(environment).model_dump()
    declared = set(suite.implementation_files)
    issues = [{"path": path, "kind": "missing_application_file",
               "message": "Required image bootstrap file is absent from the declared application interface"}
              for path in environment["required_application_files"] if path not in declared]
    for checkpoint in environment.get("compile_checkpoints", []):
        root = checkpoint["source_root"]
        if not any(path.startswith(root + "/") for path in declared):
            issues.append({"path": root, "kind": "missing_compile_component",
                           "message": "No declared application files for the compile checkpoint"})
    local_modules = set()
    for path in [*declared, *(f.path for f in suite.files)]:
        if path.endswith(".py"):
            parts = path.removeprefix("tests/").split("/")
            local_modules.update({parts[0].removesuffix(".py"), PurePosixPath(path).stem})
    for path, tree in parse_test_files({f.path: f.content for f in suite.files},
                                      python_version=environment.get("python_version")).items():
        issues.extend(_import_issues(path, tree, environment, local_modules))
        issues.extend(_tool_issues(path, tree, environment))
        issues.extend(_api_issues(path, tree, environment["api"]))
        if environment["frontend_http_server"] is False and environment["frontend_base_url_env"]:
            for node in ast.walk(tree):
                if _environment_read(node, tree) == environment["frontend_base_url_env"]:
                    issues.append({"path": path, "line": node.lineno, "kind": "frontend_server_unavailable",
                        "message": "The image declares no prestarted frontend HTTP server for this URL variable"})
    return issues


def validate_suite_environment(suite, environment):
    issues = suite_environment_issues(suite, environment)
    if issues:
        detail = "; ".join(f"{i['path']}:{i.get('line', 0)}: {i['message']}" for i in issues[:8])
        raise ValueError("Project environment preflight failed: " + detail)
