"""An approved supplemental suite with immutable, reference-validated evidence.

The reference report is harness evidence only. It must never enter a model prompt.
These cases do not claim acceptance-criterion coverage for an independent suite.
"""

from hashlib import sha256
import json

from pydantic import Field, model_validator

from orchestrator.build_coverage import python_test_names
from orchestrator.build_discovery import CASE_LINE, COUNT_LINE, covered_cases_ran, discovery_case
from orchestrator.build_models import BuildTestFile, UnittestDiscoveryCommand
from orchestrator.build_sandbox import validate_artifact
from orchestrator.project_build import TestManifest, execution_settled, matching_image_checks, sandbox_stage_results
from orchestrator.schemas import Record
from orchestrator.store import digest


POLICY = "reference-validated-regression-suite-v2"
REGRESSION_POLICY = POLICY
CAPACITY_POLICY = "supplemental-test-capacity-v1"
TEST_BYTES = 49152
INDEPENDENT_TEST_BYTES = 32768


def capacity_binding():
    """The only supported extension; independent tests retain their old cap."""
    return {"policy": CAPACITY_POLICY, "test_bytes": TEST_BYTES,
            "independent_test_bytes": INDEPENDENT_TEST_BYTES, "test_files": 15}


class SupplementalTestManifest(TestManifest):
    byte_limit = TEST_BYTES


RegressionManifest48 = SupplementalTestManifest


class RegressionCase(Record):
    test_file: str = Field(min_length=1, max_length=500)
    test_name: str = Field(min_length=1, max_length=300)


class RegressionSuite48(Record):
    implementation_files: list[str] = Field(max_length=128)
    files: list[BuildTestFile] = Field(min_length=1, max_length=15)
    commands: list[UnittestDiscoveryCommand] = Field(min_length=1, max_length=1)
    cases: list[RegressionCase] = Field(min_length=1, max_length=128)

    @property
    def coverage(self):
        """Expose discovery references without inventing acceptance criteria."""
        return self.cases

    @model_validator(mode="after")
    def complete_discovery(self):
        manifest = SupplementalTestManifest(summary="Approved supplemental regression tests", files=self.files)
        validate_artifact(manifest)
        if any(not file.path.endswith(".py") for file in self.files):
            raise ValueError("Regression suites contain Python tests and helpers only")
        names = python_test_names({file.path: file.content for file in self.files})
        expected = {(path, name) for path, methods in names.items() for name in methods}
        actual = [(case.test_file, case.test_name) for case in self.cases]
        if (set(actual) != expected or len(actual) != len(expected)
                or any(discovery_case(case) is None for case in self.cases)):
            raise ValueError("Regression cases must name every discovered test exactly once")
        if len(set(self.implementation_files)) != len(self.implementation_files):
            raise ValueError("Regression application inventory must be unique")
        return self


def regression_cases_passed(receipt, suite):
    """Require all and only the approved discovery IDs, each passing once."""
    if not covered_cases_ran(receipt, suite):
        return False
    log = receipt["commands"][0].get("stderr") or ""
    rows = [match for line in log.splitlines() if (match := CASE_LINE.fullmatch(line))]
    observed = [row["qualname"] if row["qualname"].endswith("." + row["method"])
                else row["qualname"] + "." + row["method"] for row in rows]
    expected = {discovery_case(case) for case in suite.cases}
    return (len(observed) == len(expected) and set(observed) == expected
            and COUNT_LINE.findall(log) == [str(len(expected))])


def validate_regression_suite(value, bundle, paths):
    """Validate approved suite bytes and reference evidence, without executing code."""
    fields = {"policy", "suite", "suite_digest", "image_id", "reference_digest",
              "source_files_sha256", "reference_report", "capacity"}
    if (not isinstance(value, dict) or value.keys() != fields or value["policy"] != POLICY
            or value["capacity"] != capacity_binding()
            or value["image_id"] != (bundle.get("sandbox") or {}).get("image_id")
            or value["reference_digest"] != (bundle.get("project_reference") or {}).get("digest")
            or not isinstance(value["suite"], dict)
            or value["suite"].keys() != {"files", "commands", "cases"}
            or value["suite_digest"] != digest(value["suite"])):
        raise ValueError("Supplemental regression suite differs from its approved task, image or reference")
    suite = RegressionSuite48(implementation_files=list(paths), **value["suite"])
    hashes = {file.path: sha256(file.content.encode("utf-8")).hexdigest() for file in suite.files}
    if value["source_files_sha256"] != hashes:
        raise ValueError("Supplemental regression test bytes changed")
    evidence = value["reference_report"]
    if (not isinstance(evidence, dict) or evidence.keys() != {"sha256", "content"}
            or not isinstance(evidence["content"], str) or len(evidence["content"].encode("utf-8")) > 131072
            or sha256(evidence["content"].encode("utf-8")).hexdigest() != evidence["sha256"]):
        raise ValueError("Supplemental regression reference report changed")
    try:
        report = json.loads(evidence["content"])
    except (TypeError, ValueError) as error:
        raise ValueError("Supplemental regression reference report is not valid JSON") from error
    receipt = report.get("receipt", {}) if isinstance(report, dict) else {}
    if (not isinstance(receipt, dict) or not isinstance(receipt.get("sandbox"), dict)
            or not isinstance(receipt.get("commands"), list) or len(receipt["commands"]) != 1
            or not isinstance(receipt["commands"][0], dict)):
        raise ValueError("Supplemental regression reference receipt is malformed")
    sandbox = receipt.get("sandbox", {})
    summaries = [summary for row in receipt.get("commands", [])
                 if (summary := sandbox_stage_results(row)) is not None]
    verification = (bundle.get("project_environment") or {}).get("verification")
    if (not isinstance(report, dict) or report.get("passed") is not True
            or not isinstance(verification, dict) or not verification.get("required_checks")
            or report.get("image_id") != value["image_id"]
            or report.get("reference_digest") != value["reference_digest"]
            or sandbox.get("image_id") != value["image_id"]
            or sandbox.get("network") != "disabled" or sandbox.get("read_only_root") is not True
            or sandbox.get("read_only_project") is not True or not execution_settled(receipt)
            or not matching_image_checks(receipt, summaries, verification)
            or not regression_cases_passed(receipt, suite)
            or any(summary.get("failures") or summary.get("quarantined") for summary in summaries)
            or len(summaries) != 1 or report.get("checks") != summaries[0].get("checks")):
        raise ValueError("Supplemental regression reference must pass every named case and required image check")
    return suite
