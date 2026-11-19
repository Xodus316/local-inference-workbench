"""Prove narrow unittest fixture errors without importing generated test code."""

import ast
from collections import Counter
import inspect
import unittest


_CASE = "unittest.TestCase"
_METHODS = {name: inspect.signature(value) for name, value in vars(unittest.TestCase).items()
            if (name.startswith("assert") or name in {"fail", "subTest"}) and inspect.isfunction(value)}
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _own_nodes(node):
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _SCOPES):
            yield from _own_nodes(child)


def class_assertion_problems(path, tree):
    """Find missing arguments on proven unbound TestCase assertion calls.

    Resolve import aliases, local class inheritance and simple receiver/method
    aliases. Unknown bases, overrides, rebinding and expanded argument lists
    deliberately receive no diagnosis. This is a proof of a call-shape error,
    not a ban on methods with names beginning with assert.
    """
    symbols = {"classmethod": "classmethod", "object": "object"}
    classes = {}

    def resolve(node, table):
        if isinstance(node, ast.Name):
            return table.get(node.id)
        if isinstance(node, ast.Attribute):
            owner = resolve(node.value, table)
            if owner == "unittest" and node.attr == "TestCase":
                return _CASE
            if owner == "builtins" and node.attr == "classmethod":
                return "classmethod"
            if owner == _CASE and node.attr in _METHODS:
                return ("assertion", node.attr)
        return None

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                symbols[alias.asname or alias.name] = alias.name if alias.name in {"unittest", "builtins"} else None
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                symbols[alias.asname or alias.name] = (_CASE if node.module == "unittest" and alias.name == "TestCase"
                    else "classmethod" if node.module == "builtins" and alias.name == "classmethod" else None)
        elif isinstance(node, ast.ClassDef):
            bases = [resolve(base, symbols) for base in node.bases]
            members = {}
            for member in _own_nodes(node):
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    members[member.name] = None
                elif isinstance(member, (ast.Assign, ast.AnnAssign)):
                    targets = member.targets if isinstance(member, ast.Assign) else [member.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            members[target.id] = resolve(member.value, symbols)
            classes[node.name] = (node, bases, members)
            symbols[node.name] = node.name
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = resolve(node.value, symbols)
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols[target.id] = value

    def lineage(name, active=()):
        if name == _CASE:
            return [_CASE, "object"]
        if name == "object":
            return ["object"]
        if not isinstance(name, str) or name not in classes or name in active:
            return None
        if classes[name][0].decorator_list or classes[name][0].keywords:
            return None
        bases = classes[name][1]
        parents = [lineage(base, (*active, name)) for base in bases]
        if not bases or any(parent is None for parent in parents):
            return None
        sequences = [list(parent) for parent in parents] + [list(bases)]
        result = [name]
        while any(sequences):
            sequences = [sequence for sequence in sequences if sequence]
            first = next((sequence[0] for sequence in sequences
                          if all(sequence[0] not in other[1:] for other in sequences)), None)
            if first is None:
                return None
            result.append(first)
            for sequence in sequences:
                if sequence[0] == first:
                    sequence.pop(0)
        return result

    problems = []
    for name, (cls, _bases, _members) in classes.items():
        mro = lineage(name)
        if mro is None or _CASE not in mro:
            continue

        def assertion(attribute):
            for base in mro:
                if base == _CASE:
                    return attribute if attribute in _METHODS else None
                if base in classes and attribute in classes[base][2]:
                    member = classes[base][2][attribute]
                    return member[1] if isinstance(member, tuple) and member[0] == "assertion" else None
            return None

        tests = sorted(member.name for member in cls.body if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and member.name.startswith("test"))
        for method in cls.body:
            if (not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    or not any(resolve(decorator, symbols) == "classmethod" for decorator in method.decorator_list)):
                continue
            positional = [*method.args.posonlyargs, *method.args.args]
            if not positional:
                continue
            nodes = list(_own_nodes(method))
            writes = Counter(node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store))
            receiver = positional[0].arg
            aliases = {receiver: ("receiver", name)} if not writes[receiver] else {}

            def local_value(node):
                if isinstance(node, ast.Name):
                    return aliases.get(node.id)
                if isinstance(node, ast.Attribute) and local_value(node.value) == ("receiver", name):
                    member = assertion(node.attr)
                    return ("assertion", member) if member else None
                return None

            for node in nodes:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and writes[target.id] == 1:
                            aliases[target.id] = local_value(node.value)
                elif isinstance(node, ast.Call):
                    target = local_value(node.func)
                    if (not target or target[0] != "assertion" or any(isinstance(arg, ast.Starred) for arg in node.args)
                            or any(keyword.arg is None for keyword in node.keywords)):
                        continue
                    try:
                        _METHODS[target[1]].bind(*[None for _ in node.args],
                                                **{keyword.arg: None for keyword in node.keywords})
                    except TypeError as error:
                        problems.append({"path": path, "line": node.lineno, "class": name,
                            "method": method.name, "names": tests, "kind": "unbound_class_assertion",
                            "assertion": target[1], "message": f"{path}:{node.lineno}: {name}.{method.name} calls "
                            f"unbound TestCase.{target[1]} through a class receiver: {error}. "
                            "TestCase assertions require an instance. In this class fixture, keep the asserted "
                            "condition and use an explicit conditional AssertionError."})
    return problems
