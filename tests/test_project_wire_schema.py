"""Wire-only compatibility never waives original response validation."""

from copy import deepcopy
import unittest

from pydantic import BaseModel, Field, ValidationError

from orchestrator.project_wire_schema import project_xgrammar_schema, validate_xgrammar_projection


class StructuredResponse(BaseModel):
    source: str = Field(min_length=1, max_length=40, pattern=r"^[^\r]*$")


class WireSchemaTests(unittest.TestCase):
    def test_projection_preserves_native_escape_semantics_and_original_validation(self):
        original = StructuredResponse.model_json_schema()
        before = deepcopy(original)
        wire, proof = project_xgrammar_schema(original)
        self.assertEqual(original, before)
        self.assertEqual(wire["properties"]["source"], {"title": "Source", "type": "string"})
        self.assertEqual(len(proof["removed_keywords"]), 3)
        self.assertTrue(proof["original_runtime_validation_required"])
        self.assertFalse(proof["native_value_acceptance_credit"])
        text = 'line\n"quote"\\path π'
        self.assertEqual(StructuredResponse.model_validate({"source": text}).source, text)
        for invalid in ("", "x" * 41, "line\rbreak"):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                StructuredResponse.model_validate({"source": invalid})
        self.assertEqual(validate_xgrammar_projection(original, wire, proof), proof)

    def test_nested_const_enum_and_required_pairs_remain_bound(self):
        original = {"type": "object", "properties": {"changes": {"type": "array", "items": {"anyOf": [
            {"type": "object", "properties": {"target": {"type": "string", "const": "A"},
             "digest": {"type": "string", "const": "a" * 64},
             "source": {"type": "string", "minLength": 1}}, "required": ["target", "digest", "source"],
             "additionalProperties": False}]}}, "a/b~c": {"type": "string", "pattern": ".+"}},
            "const": {"type": "string", "minLength": 17},
            "examples": [{"type": "string", "minLength": 19}]}
        wire, proof = project_xgrammar_schema(original)
        self.assertEqual(wire["const"], original["const"])
        self.assertEqual(wire["examples"], original["examples"])
        variant = wire["properties"]["changes"]["items"]["anyOf"][0]
        self.assertEqual(variant["properties"]["target"]["const"], "A")
        self.assertEqual(variant["properties"]["digest"]["const"], "a" * 64)
        self.assertEqual(variant["required"], ["target", "digest", "source"])
        self.assertFalse(variant["additionalProperties"])
        self.assertIn("/properties/a~1b~0c/pattern", [row["path"] for row in proof["removed_keywords"]])

    def test_rehashed_omission_or_schema_tampering_is_rejected(self):
        original = StructuredResponse.model_json_schema()
        wire, proof = project_xgrammar_schema(original)
        for change in ("constraint", "ledger", "credit"):
            altered, altered_proof = deepcopy(wire), deepcopy(proof)
            if change == "constraint":
                altered["required"] = []
            elif change == "ledger":
                altered_proof["removed_keywords"].pop()
            else:
                altered_proof["native_value_acceptance_credit"] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_xgrammar_projection(original, altered, altered_proof)

    def test_unchanged_schema_has_empty_ledger_and_false_credit(self):
        original = {"type": "string", "enum": ["a", "b"]}
        wire, proof = project_xgrammar_schema(original)
        self.assertEqual(wire, original)
        self.assertEqual(proof["removed_keywords"], [])
        self.assertFalse(proof["native_value_acceptance_credit"])


if __name__ == "__main__":
    unittest.main()
