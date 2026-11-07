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
}


class Generation(Record):
    context: int = Field(default=32768, ge=2048, le=262144)
    output_tokens: int | None = Field(default=None, ge=1, le=131072)
    think: bool | Literal["low", "medium", "high", "xhigh"] | None = True
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=1000)


class Research(Record):
    enabled: bool = False
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    results_per_query: int = Field(default=3, ge=1, le=5)


class Config(Record):
    ollama_url: str = "http://127.0.0.1:11434"
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
        return self
