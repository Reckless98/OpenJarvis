"""Tests for the cockpit-only browser backend."""

from __future__ import annotations

import subprocess

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.cockpit_app import create_cockpit_app  # noqa: E402
from openjarvis.tools import filip_cockpit  # noqa: E402


def test_cockpit_app_health_without_engine() -> None:
    client = TestClient(create_cockpit_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "cockpit"}


def test_cockpit_app_frontend_bootstrap_endpoints() -> None:
    client = TestClient(create_cockpit_app())
    assert client.get("/v1/models").json() == {"data": []}
    assert client.get("/v1/info").json()["engine"] == "cockpit"
    assert client.get("/v1/savings").json()["per_provider"] == []
    assert client.get("/v1/managed-agents").json() == {"agents": []}
    assert client.get("/v1/approvals/pending").json() == {"actions": []}


def test_cockpit_app_runs_repo_status(monkeypatch, tmp_path) -> None:
    def fake_run(args, *, cwd, input_text="", timeout=120):
        assert args == ["git", "status", "--short", "--branch"]
        assert cwd == tmp_path
        return subprocess.CompletedProcess(args, 0, stdout="## cockpit\n", stderr="")

    monkeypatch.setattr(filip_cockpit, "_run_command", fake_run)

    client = TestClient(create_cockpit_app())
    resp = client.post(
        "/v1/cockpit/run",
        json={
            "command": "show repo status",
            "repo_path": str(tmp_path),
            "dry_run": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "safe_shell"
    assert data["success"] is True
    assert data["result"] == "## cockpit"
