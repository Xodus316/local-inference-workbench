"""Check that model-authored text edits preserve the selected source boundaries."""
import unittest

from orchestrator.project_build import ProjectArtifact, SelectedTextRepair, materialize_text_repair


BACKEND = "api/App.java"
MODEL = "api/Model.java"


def artifact(content, model="existing model"):
    return ProjectArtifact(summary="Original", files=[
        {"path": BACKEND, "content": content}, {"path": MODEL, "content": model},
    ])


def edit(before, after, path=BACKEND):
    return {"path": path, "before": before, "after": after}


def patch(*edits):
    return {"summary": "Fix the behavior", "edits": list(edits)}


class ProjectTextRepairTests(unittest.TestCase):
    def test_applies_non_overlapping_edits_in_original_order_and_preserves_crlf(self):
        original = artifact("// café\r\nold imports\r\nunchanged\r\nold call\r\n")
        replacement = materialize_text_repair(original, [BACKEND, MODEL], patch(
            edit("old call", "new call"), edit("old imports\r\n", ""),
        ))
        self.assertEqual(replacement.summary, "Fix the behavior")
        self.assertEqual([(file.path, file.content) for file in replacement.files], [
            (BACKEND, "// café\r\nunchanged\r\nnew call\r\n"),
        ])
        self.assertEqual(original.files[0].content, "// café\r\nold imports\r\nunchanged\r\nold call\r\n")

    def test_applies_changes_to_multiple_selected_files(self):
        replacement = materialize_text_repair(artifact("before"), [BACKEND, MODEL], patch(
            edit("existing model", "new model", MODEL), edit("before", "after"),
        ))
        self.assertEqual([(file.path, file.content) for file in replacement.files], [
            (BACKEND, "after"), (MODEL, "new model"),
        ])

    def test_rejects_ambiguous_anchors_including_overlapping_occurrences(self):
        for source, anchor in (("abc abc", "abc"), ("aaaa", "aaa")):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "ambiguous"):
                materialize_text_repair(artifact(source), [BACKEND], patch(edit(anchor, "new")))

    def test_rejects_overlap_between_individually_unique_anchors(self):
        original = artifact("abcdef")
        for edits in ((edit("abc", "x"), edit("cde", "y")), (edit("abc", "x"), edit("abc", "y"))):
            with self.subTest(edits=edits), self.assertRaisesRegex(ValueError, "overlap"):
                materialize_text_repair(original, [BACKEND], patch(*edits))
        self.assertEqual(original.files[0].content, "abcdef")

    def test_resolves_anchors_against_original_text_only(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            materialize_text_repair(artifact("old\r\n"), [BACKEND], patch(
                edit("old", "new"), edit("new", "newer"),
            ))
        with self.assertRaisesRegex(ValueError, "missing"):
            materialize_text_repair(artifact("old\r\n"), [BACKEND], patch(edit("old\n", "new\n")))

    def test_rejects_edits_outside_scope_and_missing_source_paths(self):
        for path in (MODEL, "api/New.java", "../App.java"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "outside its selection"):
                materialize_text_repair(artifact("old"), [BACKEND], patch(edit("old", "new", path)))
        with self.assertRaisesRegex(ValueError, "distinct existing"):
            materialize_text_repair(artifact("old"), [BACKEND, "api/New.java"], patch(edit("old", "new")))

    def test_rejects_no_op_edit_and_net_no_op(self):
        with self.assertRaisesRegex(ValueError, "unchanged edit"):
            materialize_text_repair(artifact("ab"), [BACKEND], patch(edit("a", "a")))
        with self.assertRaisesRegex(ValueError, "leaves .* unchanged"):
            materialize_text_repair(artifact("ab"), [BACKEND], patch(edit("a", "ab"), edit("b", "")))

    def test_enforces_schema_and_utf8_edit_budget(self):
        bad_patches = (
            patch(), patch(edit("", "x")), patch(edit("a" * 4097, "x")),
            patch(edit("x", "\0")), patch(*(edit(str(i), "x") for i in range(13))),
            {**patch(edit("x", "y")), "artifact": {}}, patch({**edit("x", "y"), "other": 0}),
        )
        for value in bad_patches:
            with self.assertRaises(ValueError):
                SelectedTextRepair.model_validate(value)
        with self.assertRaisesRegex(ValueError, "12288-byte edit budget"):
            SelectedTextRepair.model_validate(patch(edit("é" * 4096, "x" * 4097)))

    def test_enforces_resulting_repair_artifact_byte_limit(self):
        with self.assertRaisesRegex(ValueError, "32768-byte manifest bound"):
            materialize_text_repair(artifact("x" * 32768 + "old"), [BACKEND], patch(edit("old", "new")))
