from copy import deepcopy
import unittest
from unittest.mock import patch

from orchestrator.build_models import BuildFile
from orchestrator import project_review_anchors as anchors
from orchestrator.project_review import validate_review
from orchestrator.store import digest
from orchestrator.wire import wire_schema


def finding(anchor_id, **changes):
    return {"severity": "P1", "category": "correctness", "anchor_id": anchor_id,
            "title": "Required result missing", "description": "The current path returns no required result.",
            "fix": "Return the specified result.", **changes}


class SourceAnchorTests(unittest.TestCase):
    def setUp(self):
        self.files = [BuildFile(path="api/One.py", content='read()\r\n  return "exact"\r\n'),
                      BuildFile(path="api/Two.py", content='read()\n  return "exact"\n')]
        self.registry = anchors.source_anchor_registry(self.files)

    def anchor(self, path="api/One.py", line=2):
        return next(key for key, row in self.registry["anchors"].items()
                    if row["path"] == path and row["line"] == line)

    def test_expansion_preserves_all_prose_order_and_repeated_findings(self):
        row = finding(self.anchor(), title="Literal “title” — unchanged",
                      description="Keep café, tabs\tand line breaks\nexact.")
        native = {"findings": [row, deepcopy(row), finding(self.anchor("api/Two.py"))]}
        result, proof = anchors.expand_source_anchor_review(native, self.registry, self.files)
        for original, expanded in zip(native["findings"], result.findings):
            for field in anchors.PROSE_FIELDS:
                self.assertEqual(getattr(expanded, field), original[field])
        self.assertEqual([x.path for x in result.findings], ["api/One.py", "api/One.py", "api/Two.py"])
        self.assertEqual(result.findings[0].evidence, '  return "exact"')
        self.assertEqual(validate_review(result, self.files), result)
        self.assertFalse(proof["canonical_is_literal_native_output"])
        self.assertEqual(proof["native_value_digest"], digest(native))
        self.assertEqual(proof["canonical_review_digest"], digest(result.model_dump()))

    def test_stale_registry_and_same_text_in_other_file_cannot_rebind(self):
        for field, value in (("path", "api/Two.py"), ("line", 1), ("evidence", "invented"),
                             ("start_character", 8)):
            forged = deepcopy(self.registry)
            forged["anchors"][self.anchor()][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "stale or altered"):
                anchors.expand_source_anchor_review({"findings": [finding(self.anchor())]}, forged, self.files)
        changed = [self.files[0].model_copy(update={"content": self.files[0].content.replace("\r\n", "\n")}),
                   self.files[1]]
        self.assertNotEqual(self.registry, anchors.source_anchor_registry(changed))

    def test_long_unicode_lines_are_exact_segments_not_guessed_quotes(self):
        line = ' α"' + "é" * 1100 + "\t tail "
        files = [BuildFile(path="src/Long.vue", content=line + "\n")]
        registry = anchors.source_anchor_registry(files)
        self.assertEqual("".join(x["evidence"] for x in registry["anchors"].values()), line)
        self.assertEqual(registry["files"][0]["segmented_lines"], {"1": [1, 2, 3]})
        for key, address in registry["anchors"].items():
            review, _ = anchors.expand_source_anchor_review({"findings": [finding(key)]}, registry, files)
            self.assertEqual(review.findings[0].evidence, address["evidence"])
            self.assertLessEqual(len(review.findings[0].evidence), 500)

    def test_foreign_anchor_rejected_and_valid_rows_preserved_on_both_sides(self):
        native = {"findings": [finding(self.anchor()), finding("foreign:L2"),
                               finding(self.anchor("api/Two.py"), title="Other valid finding")]}
        with self.assertRaises(anchors.SourceAnchorError) as raised:
            anchors.expand_source_anchor_review(native, self.registry, self.files)
        self.assertEqual(raised.exception.finding_index, 1)
        self.assertEqual([x.path for x in raised.exception.validated_findings], ["api/One.py", "api/Two.py"])

    def test_schema_bounds_extra_coordinates_and_blank_prose_remain_strict(self):
        for changes in ({"path": "api/Two.py"}, {"evidence": "invented"}, {"line": 1},
                        {"title": "x" * 121}, {"description": "x" * 901}, {"fix": "x" * 601},
                        {"description": " \t "}, {"severity": "P4"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                anchors.expand_source_anchor_review({"findings": [finding(self.anchor(), **changes)]},
                                                    self.registry, self.files)
        with self.assertRaises(ValueError):
            anchors.expand_source_anchor_review({"findings": [finding(self.anchor())] * 13}, self.registry, self.files)

    def test_grounded_rows_survive_another_rows_schema_failure(self):
        native = {"findings": [finding(self.anchor(), severity="P2"),
                               finding(self.anchor(), description="x" * 901)]}
        rows = anchors.grounded_review_findings(native, self.registry, self.files)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].severity, "P2")
        self.assertEqual(native["findings"][1]["description"], "x" * 901)

    def test_wire_enum_and_compact_contract_reconstruct_exact_schema(self):
        schema = wire_schema(anchors.source_anchor_review_type(self.registry))
        view = anchors.source_anchor_contract_view(self.registry)
        rebuilt = deepcopy(view["contract_without_duplicate_anchor_enum"])
        ids = schema["properties"]["findings"]["items"]["properties"]["anchor_id"]["enum"]
        rebuilt["properties"]["findings"]["items"]["properties"]["anchor_id"]["enum"] = ids
        self.assertEqual(rebuilt, schema)
        self.assertEqual(set(ids), set(self.registry["anchors"]))
        self.assertEqual(view["native_schema_digest"], digest(schema))

    def test_duplicate_paths_collision_and_empty_source_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            anchors.source_anchor_registry([self.files[0], self.files[0]])
        with patch.object(anchors, "digest", return_value="f" * 64), self.assertRaisesRegex(ValueError, "collision"):
            anchors.source_anchor_registry(self.files)
        files = [BuildFile(path="src/Empty.py", content="\n \t \n")]
        registry = anchors.source_anchor_registry(files)
        result, _ = anchors.expand_source_anchor_review({"findings": []}, registry, files)
        self.assertEqual(result.findings, [])
        with self.assertRaises(ValueError):
            anchors.expand_source_anchor_review({"findings": [finding("__no_source_anchors__")]}, registry, files)


if __name__ == "__main__":
    unittest.main()
