"""Bounded model outputs for planning, independent tests and implementation."""
from pathlib import PurePosixPath
from typing import ClassVar
from pydantic import Field, model_validator
from .schemas import Record


class BuildPlan(Record):
    goal: str = Field(min_length=1, max_length=2000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    implementation_steps: list[str] = Field(min_length=1, max_length=30)
    test_strategy: list[str] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(max_length=20)
    risks: list[str] = Field(max_length=20)


class BuildFile(Record):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=60000)


class BuildArtifact(Record):
    byte_limit: ClassVar[int] = 16000
    summary: str = Field(min_length=1, max_length=2000)
    files: list[BuildFile] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def bounded_files(self):
        paths = [item.path for item in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("Build file paths must be unique")
        if sum(len(item.content.encode("utf-8")) for item in self.files) > self.byte_limit:
            raise ValueError(f"Build files exceed the {self.byte_limit}-byte manifest bound")
        return self


class BuildProject(BuildArtifact):
    """Assembled implementation plus independently bounded frozen tests.

    Model output still uses BuildArtifact (16,000 bytes / 40 files). The
    accumulated test suite has its own 16,000-byte / 15-file limit.
    """
    byte_limit: ClassVar[int] = 32000
    files: list[BuildFile] = Field(min_length=1, max_length=55)


class BuildTestFile(BuildFile):
    path: str = Field(min_length=1, max_length=500, pattern=r"^tests/")


class BuildTestSuite(Record):
    implementation_files: list[str] = Field(min_length=1, max_length=40,
        description="Implementation path names only; another model writes their source after test validation.")
    files: list[BuildTestFile] = Field(min_length=1, max_length=15,
        description="Test files only, all under tests/. Never include any path listed in implementation_files.")
    commands: list[str] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def valid_commands(self):
        if any(
            not c.strip() or len(c) > 500 or any(x in c for x in "\x00\r\n")
            for c in self.commands
        ):
            raise ValueError("Tests require bounded single-line container commands")
        for item in self.files:
            path = PurePosixPath(item.path)
            if not item.path.startswith("tests/") or (
                path.suffix == ".py" and not path.name.startswith("test_")
            ):
                raise ValueError(
                    "Independent tests belong under tests/; Python test files must be named test_*.py"
                )
        BuildArtifact(summary="Independent tests", files=self.files)
        return self


class BuildTestCoverage(Record):
    criterion: int = Field(ge=1, le=20)
    test_file: str = Field(min_length=1, max_length=500)
    test_name: str = Field(min_length=1, max_length=300)
    scenario: str = Field(min_length=1, max_length=1000)
    expected_result: str = Field(min_length=1, max_length=1000)
    catches: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def meaningful_text(self):
        if any(
            not getattr(self, name).strip()
            for name in (
                "test_file",
                "test_name",
                "scenario",
                "expected_result",
                "catches",
            )
        ):
            raise ValueError("Test coverage descriptions must contain text")
        return self


class ReviewedBuildTestSuite(BuildTestSuite):
    coverage: list[BuildTestCoverage] = Field(min_length=1, max_length=80)
    assumptions: list[str] = Field(max_length=20)


class BuildTestSyntaxRepair(Record):
    files: list[BuildTestFile] = Field(min_length=1, max_length=15)


class BuildTestCoverageRepair(Record):
    files: list[BuildTestFile] = Field(min_length=1, max_length=15)
    coverage: list[BuildTestCoverage] = Field(min_length=1, max_length=80)


class RepairAdvice(Record):
    diagnosis: list[str] = Field(min_length=1, max_length=10)
    changes: list[str] = Field(min_length=1, max_length=20)
    verification: list[str] = Field(min_length=1, max_length=10)


class DiagnosedBuildRepair(Record):
    diagnosis: RepairAdvice
    artifact: BuildArtifact
