"""Recognize a mapped MongoDB decimal increment behind a failed smoke request."""

import re

from .store import digest


TARGET_ID = "mongo-decimal-increment"
_JAVA_NAME = r"[A-Za-z_$][\w$]*"
_DOCUMENT = re.compile(r"(?m)^\s*@(?:[\w.]+\.)?Document\b[^;{}]*?\bclass\s+(" + _JAVA_NAME + ")")
_UPDATE = re.compile(r"\b(?:Update|var)\s+(?P<variable>" + _JAVA_NAME + r")\s*=\s*new\s+Update\s*\(\s*\)(?P<chain>[^;]*);")
_INC = re.compile(r'\.inc\s*\(\s*"(' + _JAVA_NAME + r')"\s*,')
_DECIMAL = re.compile(r"(?m)^\s*(?P<annotations>(?:@[^;\r\n]+\s+)*)"
                      r"(?:private|protected|public)\s+(?:java\.math\.)?BigDecimal\s+(" + _JAVA_NAME + r")\s*[;=]")
_TYPE_ERROR = re.compile(r"Cannot (?:increment with non-numeric argument|apply \$inc to a value of non-numeric type)", re.I)
_DUPLICATE = re.compile(r"\b(?:E11000|DuplicateKeyException)\b|\bcode[=: ]+11000\b", re.I)
_MISLEADING_409 = re.compile(r"\b409\s*(?:!=\s*2\d\d|not found in \(\s*2\d\d\b)", re.I)
_WRITE_PATH = re.compile(r"\b(?:POST|PATCH|PUT) (/[A-Za-z0-9._~/{\}$-]+)")


def _mapping_findings(files):
    java = {item.path: re.sub(r"/\*[\s\S]*?\*/|(?m:^[ \t]*//[^\n]*)", "", item.content)
            for item in files if item.path.endswith(".java")}
    documents = {}
    for path, source in java.items():
        entity = _DOCUMENT.search(source)
        if entity:
            fields = [match[2] for match in _DECIMAL.finditer(source)
                      if not re.search(r"\btargetType\s*=|\bTransient\b", match["annotations"])]
            documents.setdefault(entity[1], []).append((path, fields))

    findings = set()
    for service_path, source in java.items():
        if "org.springframework.data.mongodb.core.MongoTemplate" not in source:
            continue
        for update in _UPDATE.finditer(source):
            # Follow the declared update argument into a typed MongoTemplate call.
            call = re.search(r"\.(?:updateFirst|updateMulti|findAndModify|upsert)\s*\([^;]*?,\s*"
                             + re.escape(update["variable"]) + r"\s*,[^;]*?\b("
                             + _JAVA_NAME + r")\.class\b[^;]*?\);", source[update.end():])
            if not call or len(documents.get(call[1], [])) != 1:
                continue
            entity_path, fields = documents[call[1]][0]
            for field in set(_INC.findall(update["chain"])) & set(fields):
                findings.add((service_path, entity_path, call[1], field))
    return findings


def mongo_increment_target(candidate, summaries, receipt, reference_receipt):
    """Target one source-bound mismatch when the exact frozen suite has a passing control."""
    if not reference_receipt:
        return None
    for summary in summaries:
        checks = summary.get("checks", {})
        if (checks.get("mongodb-ready", {}).get("status") != "passed"
                or checks.get("trusted-api-startup", {}).get("status") != "passed"
                or checks.get("python-unittest", {}).get("status") != "failed"):
            continue
        for failure in summary.get("failures", []):
            if failure.get("stage") != "python-unittest" or failure.get("status") != "failed":
                continue
            context = [line for line in failure.get("api_context", []) if isinstance(line, str)]
            confirmed = any(_TYPE_ERROR.search(line) for line in context)
            conflict = next((test for test in failure.get("test_failures", [])
                             if test.get("status") == "failed"
                             and _MISLEADING_409.search(test.get("assertion") or "")
                             and "duplicate" in (test.get("assertion") or "").casefold()), None)
            if any(_DUPLICATE.search(line) for line in context) or not (conflict or confirmed):
                continue
            findings = _mapping_findings(candidate.files)
            if len(findings) != 1:
                return None
            service_path, entity_path, entity, field = findings.pop()
            if not confirmed:
                # Require the failing write to name an operation present in the implicated service.
                path = _WRITE_PATH.search(conflict["assertion"])
                if not path:
                    continue
                operation = path[1].rsplit("/", 1)[-1]
                service = next(item.content for item in candidate.files if item.path == service_path)
                if not re.search(r"\b(?:public|protected|private)\s+\w+\s+\w*"
                                 + re.escape(operation) + r"\s*\(", service, re.I):
                    continue
            diagnostics = [
                "The same frozen smoke suite passed the approved reference and failed on the candidate.",
                f"{service_path} applies MongoDB $inc to {entity}.{field}.",
                f"{entity_path} declares the BigDecimal field without an explicit BSON target type.",
                "Spring Data MongoDB can map BigDecimal to BSON string. MongoDB requires a numeric stored "
                "value and operand for $inc. Changing only the operand leaves existing strings in place.",
            ]
            guidance = (
                "Repair the persisted field and update together. Use a numeric BSON Decimal128 mapping and "
                "compatible operands, including a safe conversion of previously persisted string values if applicable, "
                "or use an atomic compare-and-set update for the existing decimal representation. Preserve exact decimal "
                "arithmetic, concurrent updates, and legitimate uniqueness and validation rules. A generic HTTP 409 "
                "response does not establish a duplicate key. Keep the frozen tests, manifest, and coverage."
            )
            if confirmed:
                diagnostics.append("The API exception reports a non-numeric operand or stored value for MongoDB $inc.")
            if conflict:
                diagnostics.append("The candidate write returned HTTP 409 with a duplicate-value response.")
            if any("collMod.validation" in line for line in context):
                diagnostics.append("The current API log also reports an invalid MongoDB collection validation command.")
                guidance += " Remove the failing collection-validation workaround from the write path."
            return {
                "id": TARGET_ID, "check": "python-unittest", "control_match": True,
                "summary": "A Spring Data MongoDB decimal mapping can cause the failed increment.",
                "diagnostics": diagnostics, "driver_type_error_observed": confirmed,
                "source_paths": sorted({service_path, entity_path}),
                "implementation_digest": digest(candidate.model_dump()),
                "receipt_digest": digest(receipt), "reference_receipt_digest": reference_receipt,
                "repair_guidance": guidance,
            }
    return None
