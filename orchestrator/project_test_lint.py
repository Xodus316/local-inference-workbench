"""Static checks that reject generated tests which cannot discriminate any implementation.

Frozen independent tests are the acceptance oracle for a Build, so a defective
test makes `completed` unreachable however good the implementation is. These
checks run before a suite is frozen and report only test-side facts: the file,
line, test names and a reason. They never inspect implementation source.
"""
from __future__ import annotations

import ast
import builtins
import re

POLICY = "generated-test-lint-v1"
ASSERTING_ATTRIBUTES = re.compile(r"^(?:assert\w*|fail\w*|subTest)$")
PLACEHOLDER_JS = re.compile(
    r"expect\(\s*(true|false|null|undefined|\d+|'[^']*'|\"[^\"]*\")\s*\)\s*\.\s*"
    r"(?:toBe|toEqual|toStrictEqual)\(\s*\1\s*\)"
)
REJECTION_LISTENER = re.compile(
    r"\baddEventListener\s*\(\s*(['\"])unhandledrejection\1\s*,\s*"
    r"(?:(?:async\s+)?(?:\(\s*(?P<paren>[\w$]+)\s*\)|(?P<arrow>[\w$]+))\s*=>"
    r"|function(?:\s+[\w$]+)?\s*\(\s*(?P<function>[\w$]+)\s*\))\s*\{"
)
JS_LITERAL_OR_COMMENT = re.compile(
    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`|//[^\r\n]*|/\*[\s\S]*?\*/"
)
BUILTINS = frozenset(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__", "__builtins__"}


def _targets(node):
    """Names bound by an assignment-like target."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            yield from _targets(item)
    elif isinstance(node, ast.Starred):
        yield from _targets(node.value)


def _bound_names(body_nodes):
    """Names a function, class or module body binds anywhere in its own scope."""
    names = set()
    stack = list(body_nodes)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue  # nested scopes bind their own names
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                names.update(_targets(target))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            names.update(_targets(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    names.update(_targets(item.optional_vars))
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.NamedExpr):
            names.update(_targets(node.target))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _parameters(function):
    arguments = function.args
    names = [a.arg for a in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)]
    names += [a.arg for a in (arguments.vararg, arguments.kwarg) if a is not None]
    return set(names)


SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(root):
    """Nodes evaluated in root's own scope; nested functions, lambdas and classes are not entered."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _scope_functions(tree):
    """Functions defined at module or class level (including inside if/try blocks)."""
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
        elif not isinstance(node, ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))


def _undefined_names(tree):
    """Load-context names that no enclosing function or module scope binds.

    Class bodies are deliberately not visible to their methods, matching Python
    scoping: a method that reads `cls` without receiving it fails at runtime.
    """
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree)):
        return []
    module = _bound_names(tree.body) | BUILTINS
    problems = []

    def visit(function, enclosing, outermost):
        visible = enclosing | _parameters(function) | _bound_names(function.body)
        for node in _own_nodes(function):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in visible:
                problems.append((outermost, node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node, visible, outermost)

    for function in _scope_functions(tree):
        visit(function, module, function)
    return problems


def _assertion_aliases(tree):
    """Class attributes bound to TestCase assertion methods, such as `eq = TestCase.assertEqual`."""
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
            if ASSERTING_ATTRIBUTES.match(node.value.attr):
                for target in node.targets:
                    aliases.update(_targets(target))
    return aliases


def _called_names(function):
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                yield func.attr
            elif isinstance(func, ast.Name):
                yield func.id


def _asserting_functions(tree):
    """Functions that assert directly or through another asserting function in the module."""
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    aliases = _assertion_aliases(tree)
    asserting = set()
    for name, function in functions.items():
        if any(isinstance(n, (ast.Assert, ast.Raise)) for n in ast.walk(function)) or any(
            ASSERTING_ATTRIBUTES.match(called) or called in aliases for called in _called_names(function)
        ):
            asserting.add(name)
    changed = True
    while changed:
        changed = False
        for name, function in functions.items():
            if name not in asserting and any(called in asserting for called in _called_names(function)):
                asserting.add(name)
                changed = True
    return asserting


def _constant_assertion(node):
    """`assertTrue(True)`, `assertEqual(1, 1)` and similar assertions that cannot fail."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("assert") and node.args):
        return False
    if not all(isinstance(arg, ast.Constant) for arg in node.args[:2]):
        return False
    values = [arg.value for arg in node.args[:2]]
    if node.func.attr in {"assertTrue", "assertIsNotNone"}:
        return bool(values[0]) if node.func.attr == "assertTrue" else values[0] is not None
    if node.func.attr in {"assertEqual", "assertEquals", "assertIs"} and len(values) == 2:
        return values[0] == values[1]
    return False


def _string_constants(function):
    return [n.value for n in ast.walk(function) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _suppresses_unhandled_rejections(text):
    """Recognize inline rejection handlers that hide errors, scoped to their body.

    This is intentionally a narrow check for the embedded DOM-test pattern we
    observed, not a JavaScript parser or a ban on normal form preventDefault.
    """
    literals = list(JS_LITERAL_OR_COMMENT.finditer(text))
    for listener in REJECTION_LISTENER.finditer(text):
        if any(part.start() <= listener.start() < part.end() for part in literals):
            continue
        event = next(value for value in listener.groupdict().values() if value)
        start = listener.end()
        cursor, depth = start, 1
        while cursor < len(text) and depth:
            literal = JS_LITERAL_OR_COMMENT.match(text, cursor)
            if literal:
                cursor = literal.end()
                continue
            depth += (text[cursor] == "{") - (text[cursor] == "}")
            cursor += 1
        if depth:
            continue
        body = JS_LITERAL_OR_COMMENT.sub(" ", text[start:cursor - 1])
        if re.search(r"(?<![\w$])" + re.escape(event) + r"\s*\.\s*preventDefault\s*\(", body):
            return True
    return False


def defects(suite):
    """Return defect diagnostics for a generated suite, empty when none are found.

    Each diagnostic names one test file and the affected test methods, so the
    test author can rewrite exactly those methods.
    """
    problems = []
    for item in suite.files:
        if not item.path.endswith(".py"):
            continue
        try:
            tree = ast.parse(item.content)
        except SyntaxError:
            continue  # syntax preflight reports these separately
        asserting = _asserting_functions(tree)
        classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        method_class = {m: c for c in classes for m in c.body
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))}

        def tests_reaching(function):
            """Test methods in the same class that call this helper, or the test itself."""
            if function.name.startswith("test"):
                return [function.name]
            owner = method_class.get(function)
            members = owner.body if owner is not None else tree.body
            return sorted(m.name for m in members
                          if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and m.name.startswith("test") and function.name in set(_called_names(m)))

        for function, name in _undefined_names(tree):
            problems.append({"path": item.path, "line": name.lineno, "names": tests_reaching(function),
                             "code": "undefined_name",
                             "message": f"{function.name} reads undefined name {name.id!r}; "
                                        "the test raises NameError for every implementation"})
        for function in method_class:
            if not function.name.startswith("test"):
                continue
            constants = _string_constants(function)
            if function.name not in asserting:
                problems.append({"path": item.path, "line": function.lineno, "names": [function.name],
                                 "code": "no_assertion",
                                 "message": f"{function.name} has no assertion, directly or through a helper, "
                                            "so it passes for any implementation"})
            elif any(_constant_assertion(n) for n in ast.walk(function)):
                problems.append({"path": item.path, "line": function.lineno, "names": [function.name],
                                 "code": "constant_assertion",
                                 "message": f"{function.name} asserts a constant that cannot fail"})
            if any(PLACEHOLDER_JS.search(text) for text in constants):
                problems.append({"path": item.path, "line": function.lineno, "names": [function.name],
                                 "code": "placeholder_dom_test",
                                 "message": f"{function.name} embeds a DOM assertion on a literal "
                                            "(for example expect(true).toBe(true)) that cannot fail"})
        for function in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if any(_suppresses_unhandled_rejections(text) for text in _string_constants(function)):
                problems.append({"path": item.path, "line": function.lineno, "names": tests_reaching(function),
                                 "code": "suppressed_unhandled_rejection",
                                 "message": f"{function.name} suppresses an unhandled promise rejection with "
                                            "preventDefault, hiding application failures such as HTTP 401/403; "
                                            "remove that handler and preserve the failing behavior"})
            if any("--passWithNoTests" in text for text in _string_constants(function)):
                problems.append({"path": item.path, "line": function.lineno, "names": tests_reaching(function),
                                 "code": "pass_with_no_tests",
                                 "message": f"{function.name} runs Vitest with --passWithNoTests, so a DOM test "
                                            "that never executes still passes"})
    return _merge(problems)


def _merge(problems):
    seen = set()
    merged = []
    for problem in problems:
        key = (problem["path"], problem["line"], problem["code"], tuple(problem["names"]))
        if key not in seen:
            seen.add(key)
            merged.append(problem)
    return merged
