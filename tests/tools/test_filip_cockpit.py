"""Tests for Filip's local Jarvis cockpit adapters."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

from openjarvis.core.registry import ToolRegistry
from openjarvis.tools import filip_cockpit
from openjarvis.tools.filip_cockpit import (
    AriaHandoffTool,
    ClaudeAdapterTool,
    CodexAdapterTool,
    FilipRouteTool,
    LumoAdapterTool,
    PerplexityAdapterTool,
    RouteDecision,
    SafeShellTool,
    lumo_offload,
    redact,
    route_text,
)


def _reload_tool_modules() -> None:
    for mod_name in list(sys.modules):
        if mod_name == "openjarvis.tools.filip_cockpit":
            importlib.reload(sys.modules[mod_name])


def test_cockpit_tools_register() -> None:
    _reload_tool_modules()
    registered = set(ToolRegistry.keys())
    assert {
        "filip_route",
        "codex_adapter",
        "claude_adapter",
        "lumo_adapter",
        "perplexity_adapter",
        "aria_handoff",
        "safe_shell",
    }.issubset(registered)


def test_route_text_selects_expected_backends() -> None:
    assert route_text("ask Codex to implement the next step").backend == "codex"
    assert route_text("ask Claude to review this diff").backend == "claude"
    assert route_text("ask Lumo to draft a commit message").backend == "lumo"
    assert route_text("search latest docs for this library").backend == "perplexity"
    assert route_text("update handoff").backend == "aria"
    assert route_text("show GPU/system status").backend == "safe_shell"


def test_safe_shell_blocks_destructive_and_secret_paths(tmp_path: Path) -> None:
    tool = SafeShellTool()
    assert tool.execute(command="rm -rf /", repo_path=str(tmp_path)).success is False
    assert tool.execute(command="cat .env", repo_path=str(tmp_path)).success is False


def test_safe_shell_allows_git_status(monkeypatch, tmp_path: Path) -> None:
    def fake_run(args, *, cwd, input_text="", timeout=120):
        assert args == ["git", "status", "--short"]
        assert cwd == tmp_path
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(filip_cockpit, "_run_command", fake_run)
    result = SafeShellTool().execute(
        command="git status --short", repo_path=str(tmp_path)
    )
    assert result.success is True


def test_route_dry_run_reports_backend(tmp_path: Path) -> None:
    result = FilipRouteTool().execute(
        query="search latest docs for OpenJarvis tool registration",
        repo_path=str(tmp_path),
        dry_run=True,
    )
    assert result.success is True
    payload = json.loads(result.content)
    assert payload["backend"] == "perplexity"


def test_aria_read_fails_gracefully_without_state(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(filip_cockpit, "ARSENAL_ROOT", tmp_path / "missing-arsenal")
    result = AriaHandoffTool().execute(action="read", repo_path=str(tmp_path))
    assert result.success is False
    assert "No .aria" in result.content


def test_missing_external_tools_fail_gracefully(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(filip_cockpit.shutil, "which", lambda _name: "")

    lumo = LumoAdapterTool().execute(task_type="summarize", repo_path=str(tmp_path))
    perplexity = PerplexityAdapterTool().execute(
        query="latest docs",
        repo_path=str(tmp_path),
    )

    assert lumo.success is False
    assert "lumo-offload" in lumo.content
    assert perplexity.success is False
    assert "pwm" in perplexity.content


def test_codex_delegate_uses_expected_exec_argv(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_which(name: str) -> str:
        return "/bin/codex" if name == "codex" else ""

    def fake_run(args, *, cwd, input_text="", timeout=120):
        calls.append((args, cwd, timeout))
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(filip_cockpit.shutil, "which", fake_which)
    monkeypatch.setattr(filip_cockpit, "_run_command", fake_run)
    result = CodexAdapterTool().execute(
        objective="implement thing",
        repo_path=str(tmp_path),
    )

    assert result.success is True
    args, cwd, timeout = calls[-1]
    assert args[:7] == [
        "/bin/codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
    ]
    assert "implement thing" in args[-1]
    assert cwd == tmp_path
    assert timeout == 600


def test_claude_review_uses_plan_mode_and_no_tools(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_which(name: str) -> str:
        return "/bin/claude" if name == "claude" else ""

    def fake_run(args, *, cwd, input_text="", timeout=120):
        calls.append((args, cwd, input_text, timeout))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(filip_cockpit.shutil, "which", fake_which)
    monkeypatch.setattr(filip_cockpit, "_run_command", fake_run)
    result = ClaudeAdapterTool().execute(
        objective="review diff",
        repo_path=str(tmp_path),
    )

    assert result.success is True
    args, cwd, input_text, timeout = calls[-1]
    assert args[:6] == ["/bin/claude", "-p", "--permission-mode", "plan", "--tools", ""]
    assert "--add-dir" in args
    assert str(tmp_path) in args
    assert "Review only" in input_text
    assert cwd == tmp_path
    assert timeout == 300


def test_lumo_rejects_unsupported_task(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        filip_cockpit.shutil,
        "which",
        lambda name: "/bin/lumo" if name == "lumo-offload" else "",
    )
    result = lumo_offload("architecture", "do it", repo=tmp_path)
    assert result.success is False
    assert "Unsupported Lumo task" in result.content


def test_redact_and_secret_detection_cover_env_and_tokens() -> None:
    assert "[REDACTED_SECRET_PATH]" in redact("cat .env.local")
    assert "[REDACTED_SECRET]" in redact("token=abc1234567890defghijklmnop")


def test_log_route_appends_to_existing_ledger(tmp_path: Path) -> None:
    aria = tmp_path / ".aria"
    aria.mkdir()
    ledger = aria / "RUN_LEDGER.jsonl"
    ledger.write_text("", encoding="utf-8")

    filip_cockpit._log_route(
        tmp_path,
        "ask Codex to implement",
        RouteDecision("codex", "coding execution requested", "delegate"),
        True,
        "ok",
    )

    content = ledger.read_text(encoding="utf-8")
    assert '"agent": "jarvis"' in content
    assert '"backend": "codex"' in content
