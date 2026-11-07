"""Bounded, opt-in detection of sustained exact repetition in thinking only."""

from __future__ import annotations

import hashlib
import re


class RepetitionStopFrame(dict):
    """Adapter termination receipt, never a fabricated native completion."""


class ThinkingRepetitionGuard:
    policy = "thinking-paragraph-v1"
    max_paragraph_chars = 8192

    def __init__(self):
        self.pending = ""
        self.discarding = False
        self.in_fence = False
        self.previous = ""
        self.repeats = 0
        self.thinking_chars = 0
        self.max_repeats = 0

    def summary(self):
        return {"policy": self.policy, "channel": "thinking", "detected": False,
                "max_consecutive_repetitions": self.max_repeats,
                "observed_thinking_chars": self.thinking_chars}

    def observe(self, text: str) -> dict | None:
        self.thinking_chars += len(text)
        # Process incrementally even if a backend sends a large single frame.
        for offset in range(0, len(text), 1024):
            self.pending += text[offset:offset + 1024]
            while match := re.search(r"\n[ \t\r]*\n", self.pending):
                block, self.pending = self.pending[:match.start()], self.pending[match.end():]
                skip = self.discarding
                self.discarding = False
                was_fenced = self.in_fence
                if len(re.findall(r"(?m)^\s*(?:```|~~~)", block)) % 2:
                    self.in_fence = not self.in_fence
                normalized = " ".join(block.split())
                if (skip or was_fenced or self.in_fence or "```" in block or "~~~" in block
                        or not 80 <= len(normalized) <= 4096):
                    self.previous, self.repeats = "", 0
                    continue
                self.repeats = self.repeats + 1 if normalized == self.previous else 1
                self.previous = normalized
                self.max_repeats = max(self.max_repeats, self.repeats)
                if self.repeats >= 8 and self.repeats * len(normalized) >= 1024:
                    return {**self.summary(), "detected": True,
                            "consecutive_repetitions": self.repeats,
                            "paragraph_chars": len(normalized),
                            "paragraph_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
                            "observed_thinking_chars": self.thinking_chars,
                            "termination": "stream_closed_by_guard",
                            "native_completion": "not_observed"}
            if len(self.pending) > self.max_paragraph_chars:
                # Keep a possible split delimiter; do not score oversized blocks.
                dropped, self.pending = self.pending[:-128], self.pending[-128:]
                if len(re.findall(r"(?m)^\s*(?:```|~~~)", dropped)) % 2:
                    self.in_fence = not self.in_fence
                self.discarding = True
                self.previous, self.repeats = "", 0
        return None
