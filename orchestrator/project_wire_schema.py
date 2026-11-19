"""Explicit decoder compatibility projection, never a runtime schema change.

Some pinned xgrammar versions reject JSON escapes when a string has length or
pattern constraints. An approved adapter may omit those keywords from its wire
schema while retaining the exact original schema and validating the response
with its original model. This module neither selects a backend nor accepts a
native value; transport equality and original runtime validation are separate.
"""

from copy import deepcopy

from .project_native_content import digest, encode


POLICY = "xgrammar-string-constraints-runtime-v1"
STRING_KEYWORDS = ("maxLength", "pattern", "minLength")
_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
_SINGLE = {"additionalProperties", "unevaluatedProperties", "propertyNames", "contains",
           "additionalItems", "unevaluatedItems", "not", "if", "then", "else"}
_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}


def _pointer(value):
    return value.replace("~", "~0").replace("/", "~1")


def project_xgrammar_schema(original_schema):
    """Return a new wire schema and exact omission ledger; do not mutate input.

Only schema positions are traversed. Literal const/enum/default/example values
remain literal, even if their data happens to resemble a schema object.
"""
    if not isinstance(original_schema, dict):
        raise ValueError("A structured decoder requires an original JSON schema object")
    encode(original_schema)  # Reject non-JSON/non-finite schema values.
    wire = deepcopy(original_schema)
    removed = []

    def visit(schema, path=""):
        if not isinstance(schema, dict):
            return
        if schema.get("type") == "string":
            for key in STRING_KEYWORDS:
                if key in schema:
                    removed.append({"path": path + "/" + key, "value": schema.pop(key)})
        for key, child in schema.items():
            child_path = path + "/" + _pointer(key)
            if key in _MAPS and isinstance(child, dict):
                for name, subschema in child.items():
                    visit(subschema, child_path + "/" + _pointer(name))
            elif key in _SINGLE or key == "items" and isinstance(child, dict):
                visit(child, child_path)
            elif (key in _LISTS or key == "items") and isinstance(child, list):
                for index, subschema in enumerate(child):
                    visit(subschema, child_path + "/" + str(index))

    visit(wire)
    proof = {"policy": POLICY, "decoder": "xgrammar",
             "original_schema_digest": digest(original_schema), "wire_schema_digest": digest(wire),
             "removed_keywords": removed, "original_runtime_validation_required": True,
             "native_value_acceptance_credit": False}
    return wire, proof


def validate_xgrammar_projection(original_schema, wire_schema, proof):
    """Replay the exact approved wire transformation and its full ledger."""
    expected_wire, expected_proof = project_xgrammar_schema(original_schema)
    if encode(wire_schema) != encode(expected_wire) or encode(proof) != encode(expected_proof):
        raise ValueError("Native wire schema or decoder omission evidence changed")
    return deepcopy(expected_proof)
