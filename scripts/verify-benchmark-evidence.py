"""Check the synthetic evidence archive without executing benchmark submissions."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path


class EvidenceError(ValueError):
    pass


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"Cannot read JSON: {path.name}") from exc


def checked_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise EvidenceError("Invalid manifest path")
    candidate = Path(relative)
    if candidate.is_absolute() or any(p in {"..", ".git"} for p in candidate.parts):
        raise EvidenceError("Manifest path escapes the archive")
    target = root / candidate
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise EvidenceError("Manifest path escapes the archive") from exc
    if target.is_symlink() or not target.is_file():
        raise EvidenceError(f"Missing regular evidence file: {relative}")
    return target


def check_manifest(repo):
    bundle = repo / "benchmarks/evidence/v1"
    manifest = load_json(bundle / "manifest.json")
    if manifest.get("schema_version") != 1:
        raise EvidenceError("Unsupported evidence manifest version")
    seen = set()
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise EvidenceError("Empty evidence manifest")
    for entry in entries:
        relative = entry.get("path")
        if relative in seen:
            raise EvidenceError(f"Duplicate manifest entry: {relative}")
        seen.add(relative)
        target = checked_path(bundle, relative)
        content = target.read_bytes()
        if len(content) != entry.get("bytes") or hashlib.sha256(content).hexdigest() != entry.get("sha256"):
            raise EvidenceError(f"Evidence hash mismatch: {relative}")
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    if actual != seen | {"manifest.json"}:
        raise EvidenceError("Manifest does not inventory every evidence file")
    for entry in manifest.get("public_measurements", []):
        target = checked_path(repo, entry["path"])
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry.get("sha256"):
            raise EvidenceError(f"Public measurement hash mismatch: {entry['path']}")
    return len(seen)


def csv_rows(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def check_public_tables(repo):
    folder = repo / "docs/research"
    coding = csv_rows(folder / "implementation-attempts.csv")
    if len(coding) != 198:
        raise EvidenceError("Coding screen must retain all 198 archived draws")
    groups = defaultdict(list)
    identities = set()
    for row in coding:
        identity = (row["model_package"], row["generation_mode"], row["exercise"], row["draw"])
        if identity in identities:
            raise EvidenceError("Duplicate coding attempt")
        identities.add(identity)
        groups[identity[:2]].append(row)
        passed, total = row["hidden_passed"], row["hidden_total"]
        if bool(passed) != bool(total) or (passed and not 0 <= int(passed) <= int(total)):
            raise EvidenceError("Invalid hidden-check accounting")
    if len(groups) != 11 or any(len(rows) != 18 for rows in groups.values()):
        raise EvidenceError("Coding cohort accounting does not match 11 by 18 draws")
    expected = {
        ("Qwen 27B MTP Q4_K_M", "direct"): 12,
        ("Gemma 31B QAT", "direct"): 12,
        ("Qwen 27B GSQ IQ3_S", "direct"): 11,
        ("Gemma 26B A4B MTP Q4_K_M", "direct"): 4,
        ("Gemma 12B QAT", "direct"): 4,
        ("Gemma 12B Q4_K_M", "direct"): 3,
        ("Qwen3-Coder 30B A3B Q4_K_M", "native"): 0,
        ("Qwen3-Coder-Next UD-IQ1_M", "native"): 0,
        ("GPT-OSS 20B MXFP4", "high"): 0,
        ("GPT-OSS 20B MXFP4", "medium"): 7,
        ("GPT-OSS 20B MXFP4", "low"): 3,
    }
    if set(groups) != set(expected):
        raise EvidenceError("Unexpected coding cohorts")
    for key, count in expected.items():
        if sum(row["whole_attempt"] == "passed" for row in groups[key]) != count:
            raise EvidenceError(f"Coding headline disagrees with attempts: {key[0]} {key[1]}")

    reasoning = csv_rows(folder / "measurements.csv")
    if len(reasoning) != 24:
        raise EvidenceError("Reader screen must retain all 24 archived calls")
    groups = defaultdict(list)
    identities = set()
    for row in reasoning:
        identity = (row["model"], row["exercise"])
        if identity in identities:
            raise EvidenceError("Duplicate reader call")
        identities.add(identity)
        passed, total = int(row["checks_passed"]), int(row["checks_total"])
        if not 0 <= passed <= total or (row["qualified"] == "True") != (passed == total):
            raise EvidenceError("Invalid reader-check accounting")
        if float(row["elapsed_seconds"]) < 0 or int(row["generated_tokens"]) < 0:
            raise EvidenceError("Invalid reader measurement")
        groups[row["model"]].append(row)
    expected = {"Gemma 12B Q4_K_M": 30, "Gemma 12B Q8_0": 30,
                "Qwen 9B Q4_K_M": 30, "Qwen 9B BF16": 28}
    if set(groups) != set(expected):
        raise EvidenceError("Unexpected reader packages")
    for key, count in expected.items():
        if (len(groups[key]) != 6 or sum(int(r["checks_passed"]) for r in groups[key]) != count
                or sum(int(r["checks_total"]) for r in groups[key]) != 32):
            raise EvidenceError(f"Reader headline disagrees with calls: {key}")
    return len(coding), len(reasoning)


def check_coding_archive(repo):
    archived = load_json(repo / "benchmarks/evidence/v1/coding/results.json")
    published = csv_rows(repo / "docs/research/implementation-attempts.csv")
    columns = list(published[0])
    expected = Counter(tuple(row[key] for key in columns) for row in published)
    actual = []
    cohorts = archived["cohorts"]
    for record in archived["records"]:
        cohort = cohorts[record["cohort"]]
        row = {
            "model_package": cohort["model_package"],
            "generation_mode": cohort["generation_mode"],
            "context_tokens": cohort["generation"]["context_tokens"],
            "output_limit": cohort["generation"]["output_limit"],
            **{key: record[key] for key in ("exercise", "draw", "finish_reason", "whole_attempt", "hidden_passed", "hidden_total")},
        }
        actual.append(tuple("" if row[key] is None else str(row[key]) for key in columns))
    if Counter(actual) != expected:
        raise EvidenceError("Coding archive disagrees with the public per-attempt CSV")
    digest = hashlib.sha256((repo / "docs/research/implementation-attempts.csv").read_bytes()).hexdigest()
    if digest != archived["published_csv_sha256"]:
        raise EvidenceError("Coding archive references a different public CSV")
    return len(actual)


def check_reader_archive(repo):
    published = csv_rows(repo / "docs/research/measurements.csv")
    expected = {(row["model"], row["exercise"]): row for row in published}
    archive = load_json(repo / "benchmarks/evidence/v1/reasoning/reader-results.json")
    models = {"gemma-12b-q4": "Gemma 12B Q4_K_M", "gemma-12b-q8": "Gemma 12B Q8_0",
              "qwen-9b-q4": "Qwen 9B Q4_K_M", "qwen-9b-bf16": "Qwen 9B BF16"}
    cases = {"corrected-notes": "corrected-note extraction", "fifo": "FIFO reasoning",
             "public-query-drafting": "research query drafting", "defective-dictionary": "code review A",
             "conforming-dictionary": "code review B", "conflict-synthesis": "conflict synthesis"}
    seen = set()
    for record in archive["records"]:
        key = (models[record["model"]], cases[record["case"]])
        if key in seen or key not in expected:
            raise EvidenceError("Duplicate or unexpected archived reader call")
        seen.add(key)
        row = expected[key]
        grade, metrics = record["grade"], record["metrics"]
        if (grade["score"] != int(row["checks_passed"]) or grade["total"] != int(row["checks_total"])
                or grade["qualified"] != (row["qualified"] == "True")
                or record["finish_reason"] != row["finish_reason"]
                or metrics["output_tokens"] != int(row["generated_tokens"])
                or abs(metrics["elapsed_seconds"] - float(row["elapsed_seconds"])) > 0.000501):
            raise EvidenceError("Reader archive disagrees with the public per-call CSV")
    if seen != set(expected):
        raise EvidenceError("Reader archive omits a published call")
    return len(seen)


def check_role_summaries(repo):
    folder = repo / "benchmarks/evidence/v1"
    planning = load_json(folder / "planning/results.json")
    plans = [record for comparison in planning["comparisons"] for record in comparison["records"]]
    chosen = [row for row in plans if row["model"] == "gemma-26b-q4"]
    if len(chosen) != 8 or sum(row["ready"] for row in chosen) != 8 or len({row["case"] for row in chosen}) != 2:
        raise EvidenceError("Planner archive does not support eight ready submissions over two briefs")
    ttl = load_json(folder / "build/test-authorship/ttl-cache/results.json")["first_pass"]
    controls = list(ttl["controls"].values())
    if (ttl["model_calls"] != 1 or ttl["syntax_repair_calls"] != 0 or ttl["assertions_edited"]
            or len(controls) != 2 or any(row["status"] != "passed" or row["tests"] != 8 for row in controls)
            or len(ttl["mutations"]) != 10 or any(row["outcome"] != "detected" for row in ttl["mutations"])
            or ttl["mutation_summary"]["detected"] != 10):
        raise EvidenceError("Cache test-author archive disagrees with the first-pass selection evidence")
    return len(plans)


def verify(repo):
    files = check_manifest(repo)
    coding, reasoning = check_public_tables(repo)
    joined = check_coding_archive(repo)
    readers = check_reader_archive(repo)
    plans = check_role_summaries(repo)
    return {"evidence_files": files, "coding_draws": coding, "reader_calls": reasoning,
            "coding_archive_rows_reconciled": joined, "reader_archive_rows_reconciled": readers,
            "planning_submissions": plans, "selected_planner_ready": 8, "cache_faults_detected": 10}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = verify(args.root)
    except (EvidenceError, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"Evidence verification failed: {exc}\n")
    print(json.dumps(result, indent=2))
    print("Verified archive integrity and historical table accounting; no model or submission code ran.")


if __name__ == "__main__":
    main()
