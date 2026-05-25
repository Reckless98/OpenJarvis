"""OpenCode CLI bridge for the Filip Jarvis Cockpit (Phase 4).

OpenCode is a free / cheap multi-provider agent CLI (`opencode run`) that
can carry small conversational and drafting tasks the cockpit would
otherwise push to Claude. Filip has Go-tier access (DeepSeek-V4, GLM-5,
Kimi-K2.6, MiMo-V2.5, MiniMax-M2.7, Qwen-3.6, ...) free until 2026-06-06;
after that we transparently fall back to the always-free OSS tier.

The bridge stays read-only: it doesn't touch files, doesn't open editors,
and only invokes ``opencode run --format json <prompt>``. Output is whatever
the model said — fed back into the cockpit reply pipeline as plain text.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

from openjarvis.core.types import ToolResult

# After this date, OpenCode's Go-tier models go from free to paid. The
# bridge automatically swaps to the always-free fallback list.
_GO_TIER_DEADLINE = date(2026, 6, 6)

# Go-tier — preferred while still free. Order = preference; first available
# wins when the caller doesn't pin a specific model.
_GO_TIER_MODELS: tuple[str, ...] = (
    "opencode-go/deepseek-v4-flash",
    "opencode-go/deepseek-v4-pro",
    "opencode-go/glm-5.1",
    "opencode-go/glm-5",
    "opencode-go/kimi-k2.6",
    "opencode-go/kimi-k2.5",
    "opencode-go/mimo-v2.5-pro",
    "opencode-go/mimo-v2.5",
    "opencode-go/minimax-m2.7",
    "opencode-go/minimax-m2.5",
    "opencode-go/qwen3.6-plus",
    "opencode-go/qwen3.5-plus",
)

# Always-free fallback — used after _GO_TIER_DEADLINE or when the user
# explicitly asks for the cheapest possible tier.
_FREE_TIER_MODELS: tuple[str, ...] = (
    "opencode/big-pickle",
    "opencode/deepseek-v4-flash-free",
    "opencode/nemotron-3-super-free",
)

# Single-turn `opencode run` should never take more than this. Long-running
# coding sessions belong to Codex; OpenCode is the cheap chat tier.
_TIMEOUT_SECONDS = 30


def _today() -> date:
    return datetime.now(UTC).date()


def preferred_models(today: date | None = None) -> tuple[str, ...]:
    """Return the model preference list for today (Go-tier or fallback).

    Exposed so tests can pin a fake `today` without monkeypatching the clock.
    """
    when = today or _today()
    if when <= _GO_TIER_DEADLINE:
        return _GO_TIER_MODELS + _FREE_TIER_MODELS
    return _FREE_TIER_MODELS


def _resolve_model(requested: str | None) -> str:
    """Pick a concrete model id.

    If the caller pinned one, honour it. Otherwise return the first
    preference for the current date.
    """
    if requested:
        return requested
    candidates = preferred_models()
    return candidates[0] if candidates else "opencode/big-pickle"


def opencode_run(
    prompt: str,
    repo: Path | None = None,
    *,
    model: str | None = None,
    timeout_s: int = _TIMEOUT_SECONDS,
) -> ToolResult:
    """Run a single OpenCode turn and return its reply as a ToolResult."""
    _ = repo  # symmetry with other bridges; opencode doesn't need it for chat
    if not prompt.strip():
        return ToolResult(
            "opencode_bridge",
            content="No prompt supplied for OpenCode, sir.",
            success=False,
        )
    binary = shutil.which("opencode")
    if not binary:
        return ToolResult(
            "opencode_bridge",
            content=(
                "OpenCode CLI not installed. Install it with "
                "`npm i -g opencode-ai` (or follow https://opencode.ai/install) "
                "to enable the free tier."
            ),
            success=False,
        )
    chosen = _resolve_model(model)
    args = [
        binary,
        "run",
        "--model", chosen,
        "--format", "json",
        prompt,
    ]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            "opencode_bridge",
            content=f"OpenCode timed out after {timeout_s}s, sir.",
            success=False,
        )
    except OSError as exc:
        return ToolResult(
            "opencode_bridge",
            content=f"OpenCode invocation failed: {exc}",
            success=False,
        )
    if proc.returncode != 0:
        err = proc.stderr.strip()
        stderr = err.splitlines()[-1] if err else "no stderr"
        return ToolResult(
            "opencode_bridge",
            content=f"OpenCode exited {proc.returncode}: {stderr[:240]}",
            success=False,
            metadata={"model": chosen},
        )
    text = _extract_reply(proc.stdout)
    return ToolResult(
        "opencode_bridge",
        content=text or "(empty reply)",
        success=True,
        metadata={"model": chosen},
    )


def _extract_reply(stdout: str) -> str:
    """Pull the assistant text out of `opencode run --format json` output.

    Defensive: opencode's JSON shape has shifted before. Fall back to raw
    stdout if we can't parse it as JSON, and to a known-keys lookup if the
    JSON shape is unfamiliar.
    """
    raw = stdout.strip()
    if not raw:
        return ""
    if not raw.startswith("{") and not raw.startswith("["):
        return raw
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    # Common shapes — try the obvious keys first.
    for key in ("reply", "text", "content", "message", "output"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Some CLI shapes nest under {"messages": [{"role": "assistant", ...}]}.
    if isinstance(data, dict):
        messages = data.get("messages")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    body = msg.get("content") or msg.get("text") or ""
                    if isinstance(body, str) and body.strip():
                        return body.strip()
    # Last resort — give the caller the raw JSON so it isn't lost.
    return raw


__all__ = [
    "opencode_run",
    "preferred_models",
]
