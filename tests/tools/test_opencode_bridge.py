"""Tests for the OpenCode bridge (Phase 4 free-tier offload).

These tests intentionally do NOT shell out to the real `opencode` binary —
we monkeypatch `shutil.which` and `subprocess.run` so the bridge logic
(date-gated tier, model fallback, JSON parsing, error paths) is exercised
without network or CLI state.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date

from openjarvis.tools import opencode_bridge as ob


def test_preferred_models_uses_go_tier_before_deadline() -> None:
    before = date(2026, 5, 26)
    models = ob.preferred_models(today=before)
    assert models[0].startswith("opencode-go/")
    # Free tier still listed as the fallback tail.
    assert any(m.startswith("opencode/") for m in models)


def test_preferred_models_falls_back_after_deadline() -> None:
    after = date(2026, 6, 7)
    models = ob.preferred_models(today=after)
    assert all(m.startswith("opencode/") for m in models), models


def test_opencode_run_reports_missing_binary(monkeypatch) -> None:
    monkeypatch.setattr(ob.shutil, "which", lambda _name: "")
    result = ob.opencode_run("hello")
    assert result.success is False
    assert "OpenCode CLI not installed" in result.content


def test_opencode_run_parses_reply_key(monkeypatch) -> None:
    monkeypatch.setattr(
        ob.shutil, "which",
        lambda name: "/bin/opencode" if name == "opencode" else "",
    )

    def fake_run(args, *, capture_output, text, timeout, check):
        # Confirm `--format json` is included and the prompt is positional.
        assert "--format" in args and "json" in args
        assert args[-1] == "hello"
        payload = json.dumps({"reply": "Hello, sir."})
        return subprocess.CompletedProcess(args, 0, stdout=payload, stderr="")

    monkeypatch.setattr(ob.subprocess, "run", fake_run)
    result = ob.opencode_run("hello")
    assert result.success is True
    assert result.content == "Hello, sir."
    assert result.metadata.get("model", "").startswith("opencode")


def test_opencode_run_returns_raw_text_when_not_json(monkeypatch) -> None:
    monkeypatch.setattr(
        ob.shutil, "which",
        lambda name: "/bin/opencode" if name == "opencode" else "",
    )

    def fake_run(args, *, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(args, 0, stdout="Hi there.\n", stderr="")

    monkeypatch.setattr(ob.subprocess, "run", fake_run)
    result = ob.opencode_run("hello")
    assert result.success is True
    assert result.content == "Hi there."


def test_opencode_run_handles_nonzero_exit(monkeypatch) -> None:
    monkeypatch.setattr(
        ob.shutil, "which",
        lambda name: "/bin/opencode" if name == "opencode" else "",
    )

    def fake_run(args, *, capture_output, text, timeout, check):
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="auth missing\n")

    monkeypatch.setattr(ob.subprocess, "run", fake_run)
    result = ob.opencode_run("hello")
    assert result.success is False
    assert "exited 2" in result.content
    assert "auth missing" in result.content
