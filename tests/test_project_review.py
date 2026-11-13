import unittest

from orchestrator.build_models import BuildFile
from orchestrator.project_review import (
    ReviewCitationError, resolve_review_citations, review_chunks, style_findings, validate_review,
)


def finding(**changes):
    return {
        "severity": "P1", "category": "data_integrity", "path": "api/click.py", "line": 2,
        "evidence": "repository.save(click)", "title": "Concurrent clicks overwrite each other",
        "description": "Two requests can save the same incremented count.", "fix": "Increment the count atomically.",
        **changes,
    }


class ProjectReviewTests(unittest.TestCase):
    def test_chunks_preserve_all_source_when_full_prompt_size_limits_a_call(self):
        files = [BuildFile(path=name, content=content) for name, content in (
            ("api/click.py", "value = 1"), ("api/stats.py", "return stats"), ("frontend/App.vue", "a"),
        )]
        chunks = review_chunks(files, lambda items: sum(len(item.content) for item in items) <= 13)
        self.assertEqual(chunks, [[files[0]], [files[1], files[2]]])
        self.assertEqual([item for chunk in chunks for item in chunk], files)

    def test_unreviewable_file_cannot_disappear_from_coverage(self):
        files = [BuildFile(path="api/ok.py", content="short"),
                 BuildFile(path="api/oversize.py", content="source that does not fit")]
        with self.assertRaisesRegex(ValueError, "api/oversize.py.*frozen reviewer context"):
            review_chunks(files, lambda items: sum(len(item.content) for item in items) <= 8)

    def test_finding_evidence_must_match_file_and_exact_line(self):
        source = [BuildFile(path="api/click.py", content="read_count()\r\n    repository.save(click)\r\n")]
        valid = validate_review({"findings": [finding()]}, source)
        self.assertEqual(valid.findings[0].line, 2)
        self.assertEqual(validate_review({"findings": []}, source).findings, [])
        for changes in (
            {"line": 1}, {"line": 10}, {"path": "tests/test_click.py"},
            {"evidence": "repository.save(other)"}, {"evidence": "   "},
            {"severity": "unexpected"}, {"fix": "\t"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_review({"findings": [finding(**changes)]}, source)

    def test_recovery_removes_only_the_source_display_prefix(self):
        source = [BuildFile(path="api/click.py", content="read_count()\n    repository.save(click)\n")]
        for prefix in (" | ", "2 | "):
            with self.subTest(prefix=prefix):
                original = finding(evidence=prefix + "    repository.save(click)")
                result, corrections = resolve_review_citations({"findings": [original]}, source)
                self.assertEqual(result.findings[0].model_dump(),
                                 finding(evidence="    repository.save(click)"))
                self.assertEqual(corrections, [{
                    "finding_index": 0, "path": "api/click.py", "original_line": 2, "line": 2,
                    "display_prefix_removed": True,
                }])
                self.assertEqual(original["evidence"], prefix + "    repository.save(click)")
        literal = " | repository.save(click)"
        source = [BuildFile(path="api/click.py", content=f'\nprint("{literal}")')]
        result, corrections = resolve_review_citations({"findings": [finding(evidence=literal)]}, source)
        self.assertEqual(result.findings[0].evidence, literal)
        self.assertEqual(corrections, [])

    def test_recovery_relocates_unique_verbatim_quote_within_cited_file(self):
        source = [BuildFile(path="api/click.py", content="read_count()\n    repository.save(click)\n"),
                  BuildFile(path="api/other.py", content="repository.save(click)")]
        result, corrections = resolve_review_citations({"findings": [finding(line=10)]}, source)
        self.assertEqual(result.findings[0].model_dump(), finding())
        self.assertEqual(corrections, [{
            "finding_index": 0, "path": "api/click.py", "original_line": 10, "line": 2,
            "display_prefix_removed": False,
        }])
        self.assertEqual(validate_review(result, source), result)

    def test_recovery_rejects_unverifiable_findings_without_dropping_them(self):
        source = [BuildFile(path="api/click.py", content="read_count()\nrepository.save(click)\nrepository.save(click)"),
                  BuildFile(path="api/report.py", content="return total")]
        for invalid in (
            finding(line=1, evidence=" | repository.save(click)"),
            finding(line=1, evidence="repository. save(click)"),
            finding(line=1, evidence="return total"),
            finding(path="api/controllers/report.py", line=1, evidence="return total"),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ReviewCitationError) as raised:
                resolve_review_citations({"findings": [finding(), invalid]}, source)
            self.assertEqual(raised.exception.finding_index, 1)

    def test_citation_error_carries_grounded_findings_after_other_findings_fail(self):
        source = [BuildFile(path="api/click.py", content="read_count()\nrepository.save(click)\n")]
        original = {"findings": [
            finding(path="api/report.py"), finding(), finding(evidence="unsupported excerpt"),
            finding(evidence=" | read_count()", title="Concurrent reads observe stale counts"),
        ]}
        with self.assertRaisesRegex(ReviewCitationError, "api/report.py") as raised:
            resolve_review_citations(original, source)
        error = raised.exception
        self.assertEqual(error.finding_index, 0)
        self.assertEqual([entry.model_dump() for entry in error.validated_findings], [
            finding(), finding(line=1, evidence="read_count()", title="Concurrent reads observe stale counts"),
        ])
        self.assertEqual(validate_review({"findings": error.validated_findings}, source).findings,
                         error.validated_findings)
        with self.assertRaises(ReviewCitationError) as strict:
            validate_review(original, source)
        self.assertEqual([entry.model_dump() for entry in strict.exception.validated_findings], [finding()])

    def test_compressed_source_is_flagged_without_treating_data_or_generated_files_as_source(self):
        dense = "if (ready) { save(result); } " * 50
        source = [BuildFile(path=path, content=content) for path, content in (
            ("src/main.ts", "// header\n" + dense), ("src/App.vue", "<span>value</span>" * 50),
            ("src/tokens.js", 'export const token = "' + "a" * 850 + '";'),
            ("src/example.py", "# " + dense), ("schema.json", dense), ("vendor/tool.js", dense),
            ("src/tool.min.js", dense), ("src/compiler.py", "# Generated. Do not edit.\n" + dense),
        )]
        findings = style_findings(source)
        self.assertEqual([(entry.path, entry.line, entry.severity) for entry in findings],
                         [("src/main.ts", 2, "P2"), ("src/App.vue", 1, "P3")])
        self.assertEqual(validate_review({"findings": [entry.model_dump() for entry in findings]}, source).findings,
                         findings)


if __name__ == "__main__":
    unittest.main()
