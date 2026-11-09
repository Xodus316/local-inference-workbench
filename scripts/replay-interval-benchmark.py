"""Regrade the archived interval submissions in isolated local Docker containers."""
import argparse
import json
from pathlib import Path
import re
import runpy
import subprocess
import tempfile
import uuid


def docker(*args, timeout=30):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def execute(image_id, evaluator, implementation, tests, timeout):
    """Only Docker executes the supplied source; the host treats it as bytes."""
    name = "interval-evidence-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="interval-evidence-") as directory:
        root = Path(directory)
        (root / "heldout.py").write_bytes(evaluator)
        (root / "solution.py").write_bytes(implementation)
        if tests is not None:
            (root / "test_solution.py").write_bytes(tests)
        root.chmod(0o755)
        for path in root.iterdir():
            path.chmod(0o444)
        command = (["python3", "heldout.py"] if tests is None else
                   ["python3", "-m", "unittest", "-v", "test_solution"])
        args = ["run", "--rm", "--name", name, "--network", "none", "--read-only",
                "--memory", "256m", "--cpus", "1", "--pids-limit", "64",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                "--mount", f"type=bind,source={root},target=/project,readonly",
                "--workdir", "/project", "--env", "PYTHONDONTWRITEBYTECODE=1",
                "--env", "PYTHONHASHSEED=0", image_id, *command]
        try:
            result = docker(*args, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Replay timed out; outcome is inconclusive") from exc
        finally:
            docker("rm", "-f", name)
            remaining = docker("container", "inspect", name)
            absent = "no such object" in remaining.stderr.lower() or "no such container" in remaining.stderr.lower()
            if remaining.returncode == 0 or not absent:
                raise RuntimeError("Replay container cleanup could not be confirmed")
        output = result.stdout + result.stderr
        if tests is None:
            try:
                cases = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Implementation grader did not return case outcomes") from exc
            if len(cases) != 29 or any(type(row[1]) is not bool for row in cases):
                raise RuntimeError("Implementation grader returned invalid case outcomes")
            passed = sum(row[1] for row in cases)
            if result.returncode != (0 if passed == 29 else 1):
                raise RuntimeError("Implementation grader execution is inconclusive")
            return {"passed": passed, "total": 29}
        found = re.search(r"Ran (\d+) tests? in", output)
        if not found or int(found[1]) == 0 or "skipped=" in output or "errors=" in output:
            raise RuntimeError("Test suite execution is inconclusive")
        if result.returncode == 0 and re.search(r"^OK\s*$", output, re.M):
            return {"outcome": "passed", "tests": int(found[1])}
        if result.returncode == 1 and re.search(r"FAILED \(failures=\d+\)", output):
            return {"outcome": "assertion_failure", "tests": int(found[1])}
        raise RuntimeError("Test suite did not finish with a clean pass or assertion failure")


def replay(root, image):
    verifier = runpy.run_path(str(root / "scripts/verify-benchmark-evidence.py"))
    verifier["verify"](root)
    inspected = docker("image", "inspect", image)
    if inspected.returncode:
        raise RuntimeError("Image is not installed locally; this command does not pull images")
    identity = json.loads(inspected.stdout)[0]
    image_id = identity["Id"]
    folder = root / "benchmarks/evidence/v1/interval"
    spec = json.loads((folder / "replay.json").read_text(encoding="utf-8"))
    evaluator = (folder / spec["implementation_evaluator"]).read_bytes()
    controls = [(folder / spec[key]).read_bytes() for key in ("reference", "second_control")]
    recipes = json.loads((folder / spec["mutation_recipe"]).read_text(encoding="utf-8"))
    mutants = {}
    for name, (before, after) in recipes.items():
        source = controls[0].decode("utf-8")
        if source.count(before) != 1:
            raise RuntimeError(f"Mutation source no longer matches: {name}")
        mutants[name] = source.replace(before, after, 1).encode("utf-8")
    for control in controls:
        if execute(image_id, evaluator, control, None, spec["timeout_seconds"])["passed"] != 29:
            raise RuntimeError("Reference control failed; no candidate grades are valid")
    for name, source in mutants.items():
        observed = execute(image_id, evaluator, source, None, spec["timeout_seconds"])
        if observed["passed"] != spec["expected_control_qualification"][name]["heldout_passes"]:
            raise RuntimeError(f"Mutation qualification changed: {name}")
    records = []
    for model in spec["models"]:
        implementation = (folder / model["implementation"]).read_bytes()
        tests = (folder / model["tests"]).read_bytes()
        implemented = execute(image_id, evaluator, implementation, None, spec["timeout_seconds"])
        accepted = sum(execute(image_id, evaluator, source, tests, spec["timeout_seconds"])["outcome"] == "passed"
                       for source in controls)
        mutation_results = {}
        if accepted == 2:
            for name, source in mutants.items():
                outcome = execute(image_id, evaluator, source, tests, spec["timeout_seconds"])["outcome"]
                mutation_results[name] = "detected" if outcome == "assertion_failure" else "survived"
        detected = sum(value == "detected" for value in mutation_results.values()) if accepted == 2 else None
        matches = (implemented["passed"] == model["expected_implementation_passes"]
                   and accepted == model["expected_controls_accepted"]
                   and detected == model["expected_faults_detected"]
                   and mutation_results == model["expected_mutations"])
        records.append({"model": model["id"], "implementation_passes": implemented["passed"],
                        "controls_accepted": accepted, "faults_detected": detected,
                        "mutations": mutation_results, "matches_archive": matches})
    architecture = identity.get("Os", "unknown") + "/" + identity.get("Architecture", "unknown")
    historical_architecture = spec.get("historical_architecture")
    return {"benchmark_id": spec["benchmark_id"], "image_id": image_id, "architecture": architecture,
            "matches_historical_image_id": image_id == spec["historical_docker_image_id"],
            "recorded_historical_container_architecture": historical_architecture,
            "matches_historical_container_architecture": (architecture == historical_architecture
                if historical_architecture is not None else None),
            "records": records, "all_outcomes_match": all(row["matches_archive"] for row in records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="An already installed Python image name or immutable ID")
    args = parser.parse_args()
    try:
        report = replay(Path(__file__).resolve().parents[1], args.image)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyError, ValueError) as exc:
        parser.exit(1, f"Replay failed: {exc}\n")
    print(json.dumps(report, indent=2))
    if not report["all_outcomes_match"]:
        parser.exit(1, "Replay outcomes differ from the archive; historical records were not changed.\n")


if __name__ == "__main__":
    main()
