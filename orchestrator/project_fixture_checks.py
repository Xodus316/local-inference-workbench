"""Narrow, non-executing HTTP/unittest fixture checks for offline projects."""
import ast
import builtins
import copy
import io
import re
import tokenize
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


def _closing_brace(source, opening):
    fragment = source[opening:].decode("utf-8")
    lines = fragment.splitlines(keepends=True)
    depth = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(fragment).readline):
            if token.type != tokenize.OP or token.string not in {"{", "}"}:
                continue
            depth += 1 if token.string == "{" else -1
            if depth == 0:
                line, column = token.start
                prefix = "".join(lines[:line - 1]) + lines[line - 1][:column]
                return opening + len(prefix.encode("utf-8"))
    except (SyntaxError, tokenize.TokenError):
        return None
    return None


def _embedded_js_braces(source, tree):
    # Be conservative: recognize an apparent object key only in a JS template,
    # and leave names with any possible Python binding alone.
    nodes = list(ast.walk(tree))
    if any(isinstance(node, ast.alias) and node.name == "*"
           or isinstance(node, ast.Name) and node.id == "exec" for node in nodes):
        return []
    names = set(vars(builtins))
    names.update(node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store))
    names.update(node.arg for node in nodes if isinstance(node, ast.arg))
    names.update(node.asname or node.name.split(".")[0] for node in nodes if isinstance(node, ast.alias))
    names.update(node.name for node in nodes if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)))
    names.update(node.rest for node in nodes if isinstance(node, ast.MatchMapping))
    encoded = source.encode("utf-8")
    offsets = [0]
    for line in encoded.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    braces = []
    for node in nodes:
        if not isinstance(node, ast.JoinedStr):
            continue
        template = ast.get_source_segment(source, node) or ""
        if not re.search(r"\b(?:const\s+\w+\s*=|export\s+default|(?:defineConfig|createVuetify|mount)\s*\()", template):
            continue
        for field in node.values:
            if (not isinstance(field, ast.FormattedValue) or not isinstance(field.value, ast.Name)
                    or field.value.id in names or field.format_spec is None or field.conversion != -1):
                continue
            key = field.value
            start = offsets[key.lineno - 1] + key.col_offset
            prefix = re.search(rb"\{\s*$", encoded[:start])
            end = offsets[key.end_lineno - 1] + key.end_col_offset
            if prefix is None or not re.match(rb"\s*:", encoded[end:]):
                continue
            opening = prefix.start()
            closing = _closing_brace(encoded, opening)
            if closing is not None:
                braces.append((key.lineno, key.col_offset + 1, opening, closing))
    return sorted(set(braces))


def diagnostics(suite, *, python_version=None):
    sources = {file.path: file.content for file in suite.files}
    problems = []
    for filename, tree in parse_test_files(sources, python_version=python_version).items():
        for line, column, _, _ in _embedded_js_braces(sources[filename], tree):
            problems.append({"path": filename, "line": line, "column": column, "policy": POLICY,
                "kind": "embedded_js_braces",
                "message": "This JavaScript object is parsed as Python f-string interpolation. "
                           "Double just its opening and closing braces to keep it literal. "
                           "Preserve JavaScript source and all intentional Python interpolations."})
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
    sources = {file.path: file.content for file in original_files}
    before = parse_test_files(sources)
    after = parse_test_files({file.path: file.content for file in revised_files})
    if set(before) != set(after):
        raise ValueError("Fixture repair changed its Python file set")
    for path, tree in before.items():
        findings = [item for item in problems if item.get("policy") == POLICY and item["path"] == path]
        diagnosed = {(item["line"], item["column"]) for item in findings if item["kind"] == "embedded_js_braces"}
        if diagnosed:
            source = sources[path].encode("utf-8")
            positions = {pos for line, column, opening, closing in _embedded_js_braces(sources[path], tree)
                         if (line, column) in diagnosed for pos in (opening, closing)}
            for pos in sorted(positions, reverse=True):
                source = source[:pos] + source[pos:pos + 1] + source[pos:]
            tree = parse_test_files({path: source.decode("utf-8")})[path]
        allowed = {(item["class"], name) for item in findings if item["kind"] != "embedded_js_braces"
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
