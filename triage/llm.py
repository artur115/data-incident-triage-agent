"""Claude Messages API client with record / replay, for a tool-using agent loop.

Modes
  live    call the API, keep nothing
  record  call the API and save each turn's response under recordings/
  replay  serve saved turns; never call the API (CI uses this, no key needed)

Each turn is keyed by a hash of model, effort, system prompt, tool definitions and
the full message history. Tool results are deterministic for a given incident, so
a replayed run walks exactly the path the recorded run took.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_MODEL = os.environ.get("TRIAGE_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.environ.get("TRIAGE_EFFORT", "high")
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00),
          "claude-haiku-5-5": (0.10, 0.50)}  # USD per million tokens, Nov 2026


class MissingRecording(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0

    def add(self, u: dict) -> None:
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            setattr(self, k, getattr(self, k) + (u.get(k) or 0))
        self.calls += 1

    def cost_usd(self, model: str) -> float | None:
        if model not in PRICES:
            return None
        pin, pout = PRICES[model]
        return round((self.input_tokens * pin + self.cache_creation_input_tokens * pin * 1.25
                      + self.cache_read_input_tokens * pin * 0.1 + self.output_tokens * pout) / 1e6, 4)

    def to_dict(self) -> dict:
        return asdict(self)


class LLM:
    def __init__(self, mode: str, recordings: Path, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT):
        if mode not in {"live", "record", "replay"}:
            raise ValueError(mode)
        self.mode, self.dir, self.model, self.effort = mode, Path(recordings), model, effort
        self.usage = Usage()
        self._client = None

    def _key(self, system, messages, tools) -> str:
        blob = json.dumps({"model": self.model, "effort": self.effort, "system": system,
                           "tools": tools, "messages": messages}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def create(self, system: str, messages: list[dict], tools: list[dict], tag: str = "") -> dict:
        """Returns {"content": [blocks as dicts], "stop_reason": str, "usage": {...}}."""
        path = self.dir / f"{tag + '-' if tag else ''}{self._key(system, messages, tools)}.json"
        if self.mode == "replay":
            if not path.exists():
                raise MissingRecording(path.name)
            out = json.loads(path.read_text())
        else:
            out = self._call(system, messages, tools)
            if self.mode == "record":
                self.dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(out, indent=1, default=str) + "\n")
        self.usage.add(out["usage"])
        return out

    def _call(self, system, messages, tools) -> dict:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic()
        r = self._client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},   # cache the growing history between turns
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        u = r.usage
        return {"content": [b.model_dump(mode="json", exclude_none=True) for b in r.content],
                "stop_reason": r.stop_reason, "model": r.model,
                "usage": {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                          "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                          "cache_creation_input_tokens": u.cache_creation_input_tokens or 0}}
