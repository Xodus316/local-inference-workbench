"""Ground Java date formatter compiler repairs in the approved verifier APIs."""

import re


_DATE_FORMAT = "java.text.DateFormat"
_PARENTS = ("java.text.Format", "java.lang.Object")
_JACKSON = "com.fasterxml.jackson.databind"
_NON_CODE = re.compile(r'/\*.*?(?:\*/|$)|//[^\r\n]*|""".*?(?:"""|$)|"(?:\\.|[^"\\])*"|'
                       r"'(?:\\.|[^'\\])*'", re.S)
_JAVAP_METHOD = re.compile(r"^\s*(?P<modifiers>(?:\w+ )+)[\w.$\[\]]+ "
                           r"(?P<name>\w+)\((?P<parameters>[^()]*)\)", re.M)
_ANONYMOUS_FORMAT = re.compile(r"\bnew\s+(?P<type>(?:java\.text\.)?DateFormat)\s*\(\s*\)\s*\{")
_DECLARED_METHOD = re.compile(r"(?m)(?:^|(?<=[;}\n]))[ \t]*(?P<override>@(?:java\.lang\.)?Override\s+)?"
                              r"(?:public|protected)\s+(?:(?:final|synchronized)\s+)*"
                              r"[\w.$\[\]]+\s+(?P<name>\w+)\s*\((?P<parameters>[^(){}]*)\)"
                              r"(?:\s+throws\s+[\w.,\s]+)?\s*\{")


def _simple_type(name):
    return name.strip().rsplit(".", 1)[-1]


def _jvm_methods(documents, class_name):
    for match in _JAVAP_METHOD.finditer(documents.get(class_name, "")):
        modifiers = match["modifiers"].split()
        if "static" not in modifiers and "final" not in modifiers:
            types = tuple(_simple_type(p) for p in match["parameters"].split(",") if p.strip())
            yield (match["name"], types), "abstract" in modifiers


def _anonymous_findings(code, documents):
    methods = dict(_jvm_methods(documents, _DATE_FORMAT))
    if not methods:
        return
    overloads = {*methods, *(method for parent in _PARENTS for method, _ in _jvm_methods(documents, parent))}
    has_ancestors = all(parent in documents for parent in _PARENTS)
    for block in _ANONYMOUS_FORMAT.finditer(code):
        if block["type"] == "DateFormat" and not re.search(r"\bimport\s+java\.text\.DateFormat\s*;", code):
            continue
        # Stop at the closing brace of this formatter. Only direct methods
        # count, including when a formatter body contains local classes.
        level = 0
        for end in range(block.end(), len(code)):
            level += (code[end] == "{") - (code[end] == "}")
            if level < 0:
                break
        else:
            continue
        body = code[block.end():end]
        declared = set()
        for method in _DECLARED_METHOD.finditer(body):
            prefix = body[:method.start()]
            if prefix.count("{") != prefix.count("}"):
                continue
            parameters = [item.split() for item in method["parameters"].split(",") if item.strip()]
            if any(len(parts) < 2 for parts in parameters):
                continue
            signature = method["name"], tuple(_simple_type(parts[-2]) for parts in parameters)
            declared.add(signature)
            if method["override"] and has_ancestors and signature not in overloads:
                yield block.end() + method.start(), "unknown-override", signature
        for signature, abstract in methods.items():
            if abstract and signature not in declared:
                yield block.start(), "missing-abstract-method", signature


def java_date_format_findings(sources, reference):
    """Find known formatter API errors in selected Java source without changing it."""
    documents = {item["name"]: item["content"] for item in (reference or {}).get("documents", [])}
    builder = documents.get(_JACKSON + ".cfg.MapperBuilder", "")
    json_builder = documents.get(_JACKSON + ".json.JsonMapper$Builder", "")
    std_format = documents.get(_JACKSON + ".util.StdDateFormat", "")
    has_unsupported_builder_method = (json_builder and "defaultDateFormat(java.text.DateFormat)" in builder
                                      and " dateFormat(" not in builder + json_builder)
    findings = []
    for path, source in sorted(sources.items()):
        if not path.endswith(".java"):
            continue
        code = _NON_CODE.sub(lambda match: re.sub(r"[^\r\n]", " ", match[0]), source)
        for offset, kind, (name, types) in _anonymous_findings(code, documents):
            findings.append({"path": path, "line": code.count("\n", 0, offset) + 1, "kind": kind,
                             "signature": f"{name}({', '.join(types)})", "api": _DATE_FORMAT})
        if _JACKSON + ".json.JsonMapper" in code and has_unsupported_builder_method:
            for match in re.finditer(r"\bJsonMapper\.builder\s*\(\s*\)[^;]*?(\.dateFormat\s*\()", code):
                findings.append({"path": path, "line": code.count("\n", 0, match.start(1)) + 1,
                                 "kind": "unsupported-builder-method", "signature": "dateFormat(DateFormat)",
                                 "api": "MapperBuilder.defaultDateFormat(java.text.DateFormat)"})
        if (_JACKSON + ".util.StdDateFormat" in code and "(java.util.TimeZone, java.util.Locale);" in std_format
                and "StdDateFormat(java.text." not in std_format):
            for match in re.finditer(r"\bnew\s+(?:[\w.]+\.)?StdDateFormat\s*\(\s*new\s+"
                                     r"(?:java\.text\.)?SimpleDateFormat\s*\(", code):
                findings.append({"path": path, "line": code.count("\n", 0, match.start()) + 1,
                                 "kind": "unsupported-constructor", "signature": "StdDateFormat(SimpleDateFormat, ...)",
                                 "api": "StdDateFormat(java.util.TimeZone, java.util.Locale)"})
    return findings[:64]


JAVA_FORMAT_REPAIR_GUIDANCE = (
    "Resolve all Java date formatter findings against the approved API signatures. For a pattern-based "
    "Jackson formatter, keep the required pattern, time zone, and mapper settings. Configure a "
    "SimpleDateFormat directly using JsonMapper.builder().defaultDateFormat(DateFormat) or "
    "ObjectMapper.setDateFormat(DateFormat) when supported by the pinned API. This avoids an unnecessary "
    "anonymous DateFormat adapter. If the adapter is needed, implement every abstract method with the exact "
    "signature, verify every @Override against the parent, and preserve formatter cloning and thread safety. "
    "Correct the invalid method or remove an unnecessary override and its implementation as a unit."
)
