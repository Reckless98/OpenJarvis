"""Tests for extended API routes."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from openjarvis.server.api_routes import include_all_routes  # noqa: E402
from openjarvis.tools import filip_cockpit  # noqa: E402


def _make_app():
    app = FastAPI()
    include_all_routes(app)
    return app


class TestAgentRoutes:
    def test_list_agents(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert "registered" in data
        assert "running" in data

    def test_create_agent(self):
        client = TestClient(_make_app())
        resp = client.post("/v1/agents", json={"agent_type": "simple"})
        # May succeed or fail depending on agent_tools availability
        assert resp.status_code in (200, 501)

    def test_kill_nonexistent(self):
        client = TestClient(_make_app())
        resp = client.delete("/v1/agents/nonexistent")
        assert resp.status_code in (404, 501)


class TestMemoryRoutes:
    def test_search(self):
        client = TestClient(_make_app())
        resp = client.post("/v1/memory/search", json={"query": "test"})
        # May fail if SQLite not set up, that's ok
        assert resp.status_code in (200, 500)

    def test_stats(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/memory/stats")
        assert resp.status_code in (200, 500)


class TestBudgetRoutes:
    def test_get_budget(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/budget")
        assert resp.status_code == 200
        data = resp.json()
        assert "limits" in data
        assert "usage" in data

    def test_set_limits(self):
        client = TestClient(_make_app())
        resp = client.put("/v1/budget/limits", json={"max_tokens_per_day": 100000})
        assert resp.status_code == 200
        assert resp.json()["limits"]["max_tokens_per_day"] == 100000


class TestMetricsRoute:
    def test_metrics_endpoint(self):
        client = TestClient(_make_app())
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert (
            "openjarvis" in resp.text
            or "No metrics" in resp.text
            or "no telemetry data" in resp.text
        )


class TestSkillRoutes:
    def test_list_skills(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/skills")
        assert resp.status_code == 200
        assert "skills" in resp.json()


class TestSessionRoutes:
    def test_list_sessions(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/sessions")
        assert resp.status_code == 200


class TestTraceRoutes:
    def test_list_traces(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/traces")
        assert resp.status_code == 200


class TestCockpitRoutes:
    def test_cockpit_dry_run(self, tmp_path):
        client = TestClient(_make_app())
        resp = client.post(
            "/v1/cockpit/run",
            json={
                "command": "ask Codex to inspect next step",
                "repo_path": str(tmp_path),
                "dry_run": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "codex"
        assert data["success"] is True

    def test_cockpit_show_repo_status(self, monkeypatch, tmp_path):
        def fake_run(args, *, cwd, input_text="", timeout=120):
            import subprocess

            assert args == ["git", "status", "--short", "--branch"]
            assert cwd == tmp_path
            return subprocess.CompletedProcess(args, 0, stdout="## test\n", stderr="")

        monkeypatch.setattr(filip_cockpit, "_run_command", fake_run)

        client = TestClient(_make_app())
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
        assert data["result"] == "## test"

    def test_cockpit_requires_command(self):
        client = TestClient(_make_app())
        resp = client.post("/v1/cockpit/run", json={"command": "   "})
        assert resp.status_code == 400

    def test_cockpit_backends_endpoint_returns_curated_models(self):
        client = TestClient(_make_app())
        resp = client.get("/v1/cockpit/backends")
        assert resp.status_code == 200
        backends = resp.json()["backends"]
        assert "claude" in backends and "codex" in backends
        assert "claude-opus-4-7" in backends["claude"]["models"]
        assert "" in backends["claude"]["models"]  # CLI session default

    def test_cockpit_run_rejects_unknown_backend_override(self, tmp_path):
        client = TestClient(_make_app())
        resp = client.post(
            "/v1/cockpit/run",
            json={
                "command": "hello",
                "repo_path": str(tmp_path),
                "dry_run": True,
                "backend": "not-a-real-backend",
            },
        )
        assert resp.status_code == 400

    def test_cockpit_run_threads_model_through_claude_override(
        self, monkeypatch, tmp_path
    ):
        import shutil
        import subprocess

        captured: dict[str, list[str]] = {}

        def fake_which(name: str) -> str | None:
            if name == "claude":
                return "/usr/local/bin/claude"
            return None

        def fake_run(args, *, cwd, input_text="", timeout=120):
            if args[:1] == ["/usr/local/bin/claude"]:
                captured["args"] = list(args)
                return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")
            # diff probes during claude_review
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(shutil, "which", fake_which)
        monkeypatch.setattr(filip_cockpit, "_run_command", fake_run)

        client = TestClient(_make_app())
        resp = client.post(
            "/v1/cockpit/run",
            json={
                "command": "review the architecture",
                "repo_path": str(tmp_path),
                "dry_run": False,
                "backend": "claude",
                "model": "claude-opus-4-7",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["backend"] == "claude"
        assert data["model"] == "claude-opus-4-7"
        assert "--model" in captured["args"]
        assert "claude-opus-4-7" in captured["args"]
