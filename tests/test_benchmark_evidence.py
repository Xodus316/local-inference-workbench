"""Exercise archive tamper detection without executing archived model code."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("evidence_verifier", ROOT / "scripts/verify-benchmark-evidence.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class BenchmarkEvidenceTests(unittest.TestCase):
    def archive_copy(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        shutil.copytree(ROOT / "benchmarks/evidence/v1", root / "benchmarks/evidence/v1")
        (root / "docs/research").mkdir(parents=True)
        for name in ("implementation-attempts.csv", "measurements.csv"):
            shutil.copyfile(ROOT / "docs/research" / name, root / "docs/research" / name)
        return root

    def test_current_archive_and_tables_agree(self):
        result = VERIFIER.verify(ROOT)
        self.assertEqual(result["coding_archive_rows_reconciled"], 198)
        self.assertEqual(result["reader_calls"], 24)

    def test_file_tampering_fails_integrity(self):
        root = self.archive_copy()
        target = root / "benchmarks/evidence/v1/interval/reference.py"
        target.write_bytes(target.read_bytes() + b"\n# changed\n")
        with self.assertRaisesRegex(VERIFIER.EvidenceError, "hash mismatch"):
            VERIFIER.verify(root)

    def test_changed_grade_fails_even_with_updated_file_hash(self):
        root = self.archive_copy()
        bundle = root / "benchmarks/evidence/v1"
        target = bundle / "coding/results.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        record = data["records"][0]
        record["whole_attempt"] = "passed" if record["whole_attempt"] != "passed" else "failed_checks"
        target.write_text(json.dumps(data), encoding="utf-8")
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["files"]:
            if entry["path"] == "coding/results.json":
                content = target.read_bytes()
                entry.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(VERIFIER.EvidenceError, "disagrees"):
            VERIFIER.verify(root)

    def test_manifest_cannot_escape_bundle(self):
        root = self.archive_copy()
        target = root / "benchmarks/evidence/v1/manifest.json"
        manifest = json.loads(target.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../outside.json"
        target.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(VERIFIER.EvidenceError, "escapes"):
            VERIFIER.verify(root)


if __name__ == "__main__":
    unittest.main()
