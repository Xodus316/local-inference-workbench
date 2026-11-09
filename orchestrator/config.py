"""Operator-supplied local endpoints, model roles and execution limits."""
import ipaddress
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from .schemas import Record

DEFAULT_ROLES = {
    "fast_primary": "gemma4:12b",
    "fast_secondary": "qwen3.5:9b-q4_K_M",
    "deep_reasoner": "gemma4:31b-it-qat",
    "decider": "pplx-decider:review-32k",
    "planner": "gemma4:26b-a4b-it-mtp-q4_K_M",
    "editor": "qwen3.8:27b-mtp-q4_K_M",
    "reviewer": "gemma4:31b-it-qat",
    "test_author": "gemma4:31b-iq4_xs",
    "implementer": "hf.co/unsloth/Qwen3-Coder-Next-GGUF:UD-IQ1_M",
    "repairer": "qwen3.8:27b-mtp-q4_K_M",
}


class Generation(Record):
    context: int = Field(default=32768, ge=2048, le=262144)
    output_tokens: int | None = Field(default=None, ge=1, le=131072)
    think: bool | Literal["low", "medium", "high", "xhigh"] | None = True
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=1000)
    min_p: float | None = Field(default=None, ge=0, le=1)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    repetition_penalty: float | None = Field(default=None, ge=0, le=2)
    repetition_guard: bool = True


class StrataIdentity(Record):
    """Operator-verified immutable identity for one local Strata launch."""

    model: str = Field(min_length=1, max_length=300)
    engine: str = Field(min_length=1, max_length=100)
    context: int = Field(ge=2048, le=262144)
    slots: Literal[1] = 1
    runtime_sha256: str
    model_manifest_sha256: str
    template_sha256: str
    model_path_sha256: str
    config_sha256: str
    memory_mode: Literal["arena", "resident_complement", "budget_with_file_tier"]
    native_attempts_observable: bool = False
    approximation_switches: dict[str, bool | int | float | str] = Field(default_factory=dict, max_length=40)

    @field_validator(
        "runtime_sha256", "model_manifest_sha256", "template_sha256",
        "model_path_sha256", "config_sha256",
    )
    @classmethod
    def digest_value(cls, value):
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Use a lowercase SHA-256 digest")
        return value


class StrataBackend(Record):
    url: str = "http://127.0.0.1:18080"
    api_key_env: str = "STRATA_API_KEY"
    identity: StrataIdentity

    @field_validator("url")
    @classmethod
    def local_endpoint(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in ("", "/") or not parsed.hostname):
            raise ValueError("Configure a loopback HTTP Strata endpoint")
        if parsed.hostname != "localhost" and not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError("Configure a loopback Strata endpoint")
        return value.rstrip("/")

    @field_validator("api_key_env")
    @classmethod
    def environment_name(cls, value):
        if (not value or len(value) > 100 or not value.replace("_", "a").isalnum()
                or value[0].isdigit() or value.upper() != value):
            raise ValueError("Configure an uppercase environment variable name")
        return value


class Research(Record):
    # Older configurations without this field were configured for Brave.
    provider: Literal["brave", "ollama", "exa"] = "brave"
    enabled: bool = False
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    results_per_query: int = Field(default=3, ge=1, le=5)


class BuildConfig(Record):
    enabled: bool = True
    image: str = Field(default="python:3.13-alpine", min_length=1, max_length=300)
    max_repair_attempts: int = Field(default=3, ge=0, le=3)
    max_test_syntax_repairs: int = Field(default=2, ge=0, le=3)
    max_test_coverage_repairs: int = Field(default=2, ge=0, le=3)
    max_test_fixture_repairs: int = Field(default=1, ge=0, le=3)
    max_test_strengthening_attempts: int = Field(default=1, ge=0, le=2)
    max_test_mutations: int = Field(default=6, ge=1, le=12)
    mutation_timeout_seconds: int = Field(default=15, ge=1, le=60)
    test_timeout_seconds: int = Field(default=120, ge=1, le=900)
    memory_mb: int = Field(default=1024, ge=256, le=8192)
    cpus: float = Field(default=2.0, ge=0.25, le=8.0)

    @field_validator("image")
    @classmethod
    def exact_image(cls, value):
        if ":" not in value or value.startswith("-") or any(c.isspace() for c in value):
            raise ValueError("Configure a preinstalled image with an explicit tag or digest")
        return value


class Config(Record):
    build: BuildConfig = Field(default_factory=BuildConfig)
    backend: Literal["ollama", "strata"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    strata: StrataBackend | None = None
    roles: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_ROLES))
    generation: dict[str, Generation] = Field(default_factory=dict)
    repositories: list[str] = Field(default_factory=list)
    research: Research = Field(default_factory=Research)
    test_commands: dict[str, list[str]] = Field(default_factory=dict, max_length=20)
    test_timeout_seconds: int = Field(default=120, ge=1, le=600)
    run_timeout_seconds: int = Field(default=1800, ge=10, le=7200)
    queue_size: int = Field(default=8, ge=1, le=128)
    storage_bytes: int = Field(default=512_000_000, ge=50_000_000)

    @field_validator("ollama_url")
    @classmethod
    def local_endpoint(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in ("", "/") or not parsed.hostname):
            raise ValueError("Configure a local HTTP Ollama endpoint")
        if parsed.hostname != "localhost" and not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError("Configure a loopback Ollama endpoint")
        return value.rstrip("/")

    @field_validator("roles", mode="before")
    @classmethod
    def include_build_roles(cls, value):
        # Upgrade existing seven-role configurations while honoring explicit role bindings.
        if not isinstance(value, dict):
            raise ValueError("Role bindings must be an object")
        return {**{role: DEFAULT_ROLES[role] for role in ("test_author", "implementer", "repairer")}, **value}

    def generation_for(self, role):
        defaults = {}
        if role in {"implementer", "repairer"}:
            defaults = {"context": 98304, "think": None if role == "implementer" else "xhigh"}
        profile = self.generation.get(role)
        overrides = profile.model_dump(exclude_unset=True) if profile else {}
        return Generation.model_validate({**defaults, **overrides})

    @model_validator(mode="after")
    def valid_roles(self):
        if set(self.roles) != set(DEFAULT_ROLES) or not self.generation.keys() <= self.roles.keys():
            raise ValueError("Invalid role mapping")
        if not all(m.strip() and len(m) <= 300 for m in self.roles.values()):
            raise ValueError("Each role needs a model name")
        if any(not Path(p).is_absolute() for p in self.repositories):
            raise ValueError("Repository allowlist entries must be absolute")
        for name, argv in self.test_commands.items():
            if (not name.strip() or len(name) > 80 or not 1 <= len(argv) <= 60
                    or any(not arg or len(arg) > 2000 or "\0" in arg for arg in argv)):
                raise ValueError("Invalid named test command")
        if self.backend == "strata" and self.strata is None:
            raise ValueError("Configure the pinned Strata backend identity")
        if self.backend != "strata" and self.strata is not None:
            raise ValueError("Strata settings require backend=strata")
        return self
