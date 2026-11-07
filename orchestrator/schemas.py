from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Citation(Record):
    source_id: str = Field(min_length=1, max_length=80)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=6000)


class Assessment(Record):
    answer: str = Field(min_length=1, max_length=20000)
    key_claims: list[str] = Field(max_length=40)
    assumptions: list[str] = Field(max_length=20)
    uncertainties: list[str] = Field(max_length=20)
    evidence: list[Citation] = Field(max_length=40)
    verification_needed: list[str] = Field(max_length=20)


class GroundedAssessment(Record):
    """Model-facing assessment; evidence selects immutable source-line refs."""

    answer: str = Field(min_length=1, max_length=20000)
    key_claims: list[str] = Field(max_length=40)
    assumptions: list[str] = Field(max_length=20)
    uncertainties: list[str] = Field(max_length=20)
    evidence: list[str] = Field(max_length=40)
    verification_needed: list[str] = Field(max_length=20)


class PlanStep(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    title: str = Field(min_length=1, max_length=500)
    capability: Literal["inspect_repo", "propose_edit", "propose_test"]
    files: list[str] = Field(min_length=1, max_length=20)
    depends_on: list[str] = Field(max_length=20)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class CodePlan(Record):
    goal: str = Field(min_length=1, max_length=2000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(max_length=20)
    steps: list[PlanStep] = Field(min_length=1, max_length=20)
    proposed_tests: list[str] = Field(max_length=20)
    risks: list[str] = Field(max_length=20)


class ResearchQueries(Record):
    queries: list[str] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def bounded_queries(self):
        if any(
            not q.strip() or len(q) > 400 or len(q.split()) > 60 for q in self.queries
        ):
            raise ValueError("Search queries exceed the public query bound")
        return self


class Decision(Record):
    choice: Literal["A", "B", "combine", "neither"]
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def probabilities_valid(self):
        if set(self.probabilities) != {"A", "B", "combine", "neither"}:
            raise ValueError("Invalid decision choices")
        if any(not 0 <= p <= 1 for p in self.probabilities.values()):
            raise ValueError("Invalid probability")
        return self



class Source(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    text: str = Field(min_length=1, max_length=60000)
    title: str = Field(default="Attached source", max_length=300)
    kind: Literal["source", "diff", "tests", "observation", "local_document"] = "source"


class ControlFile(Record):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=16000)


class Request(Record):
    prompt: str = Field(min_length=1, max_length=20000)
    mode: Literal["auto","fast","balanced","verified","deep","plan","edit","build"] = "auto"
    sources: list[Source] = Field(default_factory=list, max_length=32)
    research: bool = False
    tests: list[str] = Field(default_factory=list, max_length=8)
    control_files: list[ControlFile] = Field(default_factory=list, max_length=40)
    repository: str | None = None
    files: list[str] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def scope(self):
        if not self.prompt.strip():
            raise ValueError("Empty prompt")
        if self.mode in {"plan","edit"}:
            if not self.repository or not self.files:
                raise ValueError("Select a repository and files")
        elif self.repository or self.files:
            raise ValueError("Repository scope requires plan or edit mode")
        if self.control_files and self.mode != "build":
            raise ValueError("Reference implementations require Build mode")
        if self.tests and (self.mode != "edit" or len(set(self.tests)) != len(self.tests)):
            raise ValueError("Select distinct tests in edit mode")
        if self.research and self.mode != "balanced":
            raise ValueError("Web research requires balanced mode")
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source identifier")
        if len({p.casefold() for p in self.files}) != len(self.files):
            raise ValueError("Duplicate selected path")
        if sum(len(s.text.encode()) for s in self.sources) > 180000:
            raise ValueError("Source text exceeds the combined input bound")
        return self
