"""Narrow, non-executing HTTP/unittest fixture checks for offline projects."""
import ast
import copy
from collections import Counter

from .build_coverage import parse_test_files

POLICY = "unittest-http-fixtures-v1"


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _name(node.value) + "." + node.attr
    return ""


def _aliases(tree, module, member):
    names = {module + "." + member}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    names.add((alias.asname or module) + "." + member)
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.asname or alias.name for alias in node.names if alias.name == member)
    return names


def _assignments(node, owner):
    values = {}
    for child in ast.walk(node):
        if isinstance(child, (ast.Assign, ast.AnnAssign)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and _name(target.value) == owner:
                    values.setdefault(target.attr, []).append(child.value)
    return values


def _reachable(start, methods):
    pending, found = [start], set()
    while pending:
        name = pending.pop()
        if name in found or name not in methods:
            continue
        found.add(name)
        pending.extend(call.func.attr for call in ast.walk(methods[name]) if isinstance(call, ast.Call)
                       and isinstance(call.func, ast.Attribute) and _name(call.func.value) == "self")
    return found


def _bearer_attributes(method):
    attributes = set()
    for node in ast.walk(method):
        if isinstance(node, (ast.JoinedStr, ast.BinOp)) and any(
                isinstance(child, ast.Constant) and isinstance(child.value, str) and "Bearer" in child.value
                for child in ast.walk(node)):
            attributes.update(child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
                              and _name(child.value) == "self")
    return attributes


def _auth_problems(filename, cls):
    methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
    fixtures = _assignments(methods["setUpClass"], "cls") if "setUpClass" in methods else {}
    for child in cls.body:
        if isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    fixtures.setdefault(target.id, []).append(child.value)
    suspects = {name for name, values in fixtures.items() if values and all(
        isinstance(value, ast.Constant) and value.value is None for value in values)}
    setup = _reachable("setUp", methods)
    setup_writes = {name for helper in setup for name in _assignments(methods[helper], "self")}
    tests = {name for name in methods if name.startswith("test_")}
    problems = []
    for attribute in sorted(suspects - setup_writes):
        writers = {name for name in tests if attribute in _assignments(methods[name], "self")}
        bearer = {name for name, method in methods.items() if attribute in _bearer_attributes(method)}
        if not writers or not bearer:
            continue
        readers = []
        for name in sorted(tests - writers):
            reached = _reachable(name, methods)
            if reached & bearer and not any(attribute in _assignments(methods[helper], "self") for helper in reached):
                readers.append(name)
        if readers:
            problems.append({"path": filename, "line": methods[sorted(bearer)[0]].lineno,
                "policy": POLICY, "kind": "cross_case_instance_auth", "class": cls.name,
                "helpers": sorted(bearer), "attribute": attribute, "readers": readers,
                "message": f"Class fixture {attribute} remains None. Other test instances cannot inherit "
                           "self assignments from earlier test methods; independently initialize shared "
                           "class credentials or per-case credentials in setup without changing test methods."})
    return problems


def _http_problems(filename, tree, cls):
    methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
    loads = _aliases(tree, "json", "loads")
    urlopen = _aliases(tree, "urllib.request", "urlopen")
    problems = []
    for name, method in methods.items():
        invoked_delete = any(isinstance(call, ast.Call) and _name(call.func) == "self." + name
            and (any(isinstance(arg, ast.Constant) and arg.value == "DELETE" for arg in call.args)
                 or any(keyword.arg == "method" and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == "DELETE" for keyword in call.keywords))
            for other in methods.values() if other.name.startswith("test_") for call in ast.walk(other))
        if not invoked_delete:
            continue
        responses = {item.optional_vars.id for node in ast.walk(method) if isinstance(node, ast.With)
            for item in node.items if isinstance(item.optional_vars, ast.Name)
            and isinstance(item.context_expr, ast.Call) and _name(item.context_expr.func) in urlopen}
        parents = {child: node for node in ast.walk(method) for child in ast.iter_child_nodes(node)}
        definitions = {target.id: node.value for node in ast.walk(method) if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Name)}
        for call in ast.walk(method):
            if not isinstance(call, ast.Call) or _name(call.func) not in loads or not call.args:
                continue
            value = call.args[0]
            variables = set()
            pending, response_read = [value], False
            while pending:
                expression = pending.pop()
                for node in ast.walk(expression):
                    if isinstance(node, ast.Name) and node.id in definitions and node.id not in variables:
                        variables.add(node.id)
                        pending.append(definitions[node.id])
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "read" and _name(node.func.value) in responses):
                        response_read = True
            if not response_read:
                continue
            guarded = False
            current = call
            while current in parents:
                current = parents[current]
                if isinstance(current, (ast.If, ast.IfExp)) and any(
                        isinstance(n, ast.Name) and n.id in variables
                        or isinstance(n, ast.Constant) and n.value == 204 for n in ast.walk(current.test)):
                    guarded = True
            for node in ast.walk(method):
                if isinstance(node, ast.If) and node.lineno < call.lineno and any(
                        isinstance(n, ast.Name) and n.id in variables
                        or isinstance(n, ast.Constant) and n.value == 204 for n in ast.walk(node.test)):
                    guarded = True
            if not guarded:
                problems.append({"path": filename, "line": call.lineno, "policy": POLICY,
                    "kind": "empty_http_json", "class": cls.name, "helpers": [name],
                    "message": "This DELETE response helper unconditionally decodes JSON from HTTP bytes. "
                               "Valid HTTP 204 responses are empty. Read the bytes first and decode JSON "
                               "only when nonempty; keep malformed nonempty JSON as a failure. "
                               "Do not change test methods, assertions or expected application behavior."})
                break
    return problems


def diagnostics(suite):
    problems = []
    for filename, tree in parse_test_files({file.path: file.content for file in suite.files}).items():
        bases = _aliases(tree, "unittest", "TestCase")
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef) and any(_name(base) in bases for base in cls.bases):
                problems.extend(_auth_problems(filename, cls))
                problems.extend(_http_problems(filename, tree, cls))
    return problems


def _assertions(tree):
    return Counter(ast.dump(node, include_attributes=False) for node in ast.walk(tree)
        if isinstance(node, ast.Assert) or isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and (node.func.attr.startswith("assert") or node.func.attr == "fail"))


def _bypasses(tree):
    markers = {"skipTest", "SkipTest", "expectedFailure", "skip", "skipIf", "skipUnless"}
    return Counter(_name(node) for node in ast.walk(tree)
                   if isinstance(node, (ast.Name, ast.Attribute)) and _name(node).rsplit(".", 1)[-1] in markers)


def _imports(tree):
    return Counter(ast.dump(node, include_attributes=False) for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom)))


def _scaffold(tree, allowed, existing):
    tree = copy.deepcopy(tree)
    added = set()
    for cls in tree.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        body = []
        for node in cls.body:
            key = (cls.name, getattr(node, "name", None))
            if isinstance(node, ast.FunctionDef) and key in allowed:
                if key not in existing:
                    if (key in added or node.name not in {"setUp", "setUpClass"} or len(node.args.args) != 1
                            or node.args.defaults or node.args.vararg or node.args.kwarg
                            or node.args.kwonlyargs or node.args.posonlyargs
                            or any(arg.annotation for arg in node.args.args) or node.returns
                            or node.type_comment or getattr(node, "type_params", [])
                            or [_name(d) for d in node.decorator_list] != (["classmethod"] if node.name == "setUpClass" else [])):
                        raise ValueError("Fixture repair added an unsupported setup method")
                    added.add(key)
                    continue
                node.body = [ast.Pass()]
            body.append(node)
        cls.body = body
    return ast.dump(tree, include_attributes=False)


def validate_repair(original_files, revised_files, problems):
    """Permit diagnosed setup/helper bodies only; preserve test ASTs and assertions."""
    before = parse_test_files({file.path: file.content for file in original_files})
    after = parse_test_files({file.path: file.content for file in revised_files})
    if set(before) != set(after):
        raise ValueError("Fixture repair changed its Python file set")
    for path, tree in before.items():
        allowed = {(item["class"], name) for item in problems if item.get("policy") == POLICY and item["path"] == path
                   for name in ["setUp", "setUpClass", *item["helpers"]] if not name.startswith("test")}
        existing = {(cls.name, node.name) for cls in tree.body if isinstance(cls, ast.ClassDef)
                    for node in cls.body if isinstance(node, ast.FunctionDef)}
        if _assertions(tree) != _assertions(after[path]):
            raise ValueError("Fixture repair changed observable assertions")
        if _bypasses(after[path]) - _bypasses(tree):
            raise ValueError("Fixture repair introduced a unittest skip or expected-failure bypass")
        if _imports(tree) != _imports(after[path]):
            raise ValueError("Fixture repair changed test imports")
        if _scaffold(tree, allowed, existing) != _scaffold(after[path], allowed, existing):
            raise ValueError("Fixture repair changed protected test methods or code outside diagnosed setup/helpers")
