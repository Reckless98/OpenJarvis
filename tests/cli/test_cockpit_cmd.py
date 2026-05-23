"""Tests for ``jarvis cockpit``."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from openjarvis.cli import cli


def test_cockpit_help() -> None:
    result = CliRunner().invoke(cli, ["cockpit", "--help"])
    assert result.exit_code == 0
    assert "Route a text request" in result.output


def test_cockpit_dry_run_json(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "cockpit",
            "--repo",
            str(tmp_path),
            "--dry-run",
            "--json",
            "ask Claude to review this diff",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["backend"] == "claude"
    assert payload["success"] is True


def test_cockpit_dry_run_without_api_keys(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = CliRunner().invoke(
        cli,
        [
            "cockpit",
            "--repo",
            str(tmp_path),
            "--dry-run",
            "--json",
            "ask Codex to implement the next step",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["backend"] == "codex"
    assert payload["success"] is True


def test_cockpit_requires_request() -> None:
    result = CliRunner().invoke(cli, ["cockpit"])
    assert result.exit_code != 0
    assert "Provide a cockpit request" in result.output
