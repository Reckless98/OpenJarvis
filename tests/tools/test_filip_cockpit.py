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


def test_route_text_routes_finance_news_weather_to_perplexity() -> None:
    """Cheap Sonar 2 lookups for current-information intents."""
    assert route_text("track Tesla stock").backend == "perplexity"
    assert route_text("how are the markets today").backend == "perplexity"
    assert route_text("price of bitcoin").backend == "perplexity"
    assert route_text("weather in Belgrade").backend == "perplexity"
    assert route_text("forecast for tomorrow").backend == "perplexity"
    assert route_text("latest news on Apple").backend == "perplexity"
    assert route_text("today's headlines").backend == "perplexity"
    assert route_text("score of the Lakers game").backend == "perplexity"


def test_route_text_routes_make_project_intent() -> None:
    """'make me a project todo-app' should go to the project scaffold tool."""
    assert route_text("make me a project todo-app").backend == "make_project"
    assert (
        route_text("create a new project called sticky-notes").backend
        == "make_project"
    )
    assert (
        route_text("scaffold a project named hello-world").backend == "make_project"
    )


def test_route_text_routes_browser_intents_to_playwright() -> None:
    """Explicit browser-automation phrasings hit the Playwright bridge."""
    assert route_text("log in to https://github.com").backend == "playwright"
    assert route_text("login to https://music.youtube.com").backend == "playwright"
    assert route_text("browse to https://news.ycombinator.com").backend == "playwright"
    assert (
        route_text("search youtube for don't tread on me by cain").backend
        == "playwright"
    )
    assert route_text("screenshot https://example.com").backend == "playwright"


def test_make_project_creates_directory_with_scaffold(tmp_path, monkeypatch) -> None:
    """make_project should create ~/Projects/<name>/ with README + git init."""
    fake_projects = tmp_path / "Projects"
    monkeypatch.setattr(filip_cockpit, "_PROJECTS_ROOT", fake_projects)
    result = filip_cockpit.make_project("make me a project called todo-demo")
    assert result.success is True
    target = fake_projects / "todo-demo"
    assert target.is_dir()
    assert (target / "README.md").is_file()
    assert (target / ".gitignore").is_file()


def test_make_project_rejects_bad_names_and_collisions(tmp_path, monkeypatch) -> None:
    fake_projects = tmp_path / "Projects"
    monkeypatch.setattr(filip_cockpit, "_PROJECTS_ROOT", fake_projects)
    # First create succeeds.
    filip_cockpit.make_project("make me a project called demo-app")
    # Collision should fail with a clear message.
    again = filip_cockpit.make_project("make me a project called demo-app")
    assert again.success is False
    assert "already exists" in again.content


def test_make_project_clarifies_when_name_missing(tmp_path, monkeypatch) -> None:
    """Bare 'make me a project' returns success=True with a question.

    The cockpit frontend triggers the speak-and-listen clarify flow only when
    `action == 'clarify'` AND `success is True` — see FilipCockpitPage. So a
    no-name request must not fail; it must ask.
    """
    fake_projects = tmp_path / "Projects"
    monkeypatch.setattr(filip_cockpit, "_PROJECTS_ROOT", fake_projects)
    blank = filip_cockpit.make_project("make me a project")
    assert blank.success is True
    assert "what shall i call" in blank.content.lower()
    # And the matching route decision is clarify, not scaffold.
    assert route_text("make me a project").action == "clarify"
    # A named ask still scaffolds.
    assert route_text("make me a project called widget").action == "scaffold"


def test_route_text_routes_factual_web_questions_to_perplexity() -> None:
    """Phase 4 widening — short factual asks should hit Sonar, not Claude."""
    assert route_text("what version of React is current").backend == "perplexity"
    assert route_text("what is the latest Next.js release").backend == "perplexity"
    assert route_text("who is the CEO of Anthropic").backend == "perplexity"
    assert route_text("when did Python 3.13 release").backend == "perplexity"
    assert route_text("docs for fastapi dependencies").backend == "perplexity"
    assert route_text("changelog for sqlalchemy 2.1").backend == "perplexity"
    assert route_text("look up the GH Actions runner image").backend == "perplexity"


def test_route_text_routes_bare_url_to_perplexity() -> None:
    """A pasted URL with no Playwright verb → Perplexity summarization."""
    assert route_text("what's at https://example.com/blog/post").backend == "perplexity"


def test_route_text_routes_terminal_intent() -> None:
    assert route_text("open terminal").backend == "terminal"
    assert route_text("launch terminal please").backend == "terminal"
    assert route_text("open a terminal window").backend == "terminal"


def test_route_text_routes_file_write_intent() -> None:
    a = route_text("write a file ~/Projects/foo.txt with hi").backend
    b = route_text("create a file /tmp/jarvis-test.txt with hello").backend
    assert a == "file_write"
    assert b == "file_write"


def test_route_text_routes_subagent_spawn_intent() -> None:
    a = route_text("spawn subagent to map the routing").backend
    b = route_text("delegate to subagent: find dead code").backend
    assert a == "subagent_spawn"
    assert b == "subagent_spawn"


def test_route_text_routes_natural_council_phrasings() -> None:
    a = route_text("what do all the models think about Rust async").action
    b = route_text("get a second opinion from the council").action
    assert a == "council"
    assert b == "council"


def test_route_text_prefers_opencode_for_short_chat_when_available(monkeypatch) -> None:
    """Short fallback asks go to OpenCode when the binary is installed."""
    monkeypatch.setattr(
        filip_cockpit.shutil,
        "which",
        lambda name: "/bin/opencode" if name == "opencode" else "",
    )
    decision = route_text("how do you feel today")
    assert decision.backend == "opencode"


def test_route_text_falls_back_to_claude_when_opencode_missing(monkeypatch) -> None:
    monkeypatch.setattr(filip_cockpit.shutil, "which", lambda _name: "")
    decision = route_text("how do you feel today")
    assert decision.backend == "claude"


def test_open_terminal_uses_x_terminal_emulator(monkeypatch) -> None:
    """open_terminal must spawn the allowlisted emulator, not the shell."""
    called: dict[str, list[str]] = {}

    class FakePopen:
        def __init__(self, args, **_kwargs):
            called["args"] = args

    monkeypatch.setattr(
        filip_cockpit.shutil, "which",
        lambda name: (
            "/bin/x-terminal-emulator"
            if name == "x-terminal-emulator"
            else ""
        ),
    )
    import subprocess as _sp

    monkeypatch.setattr(_sp, "Popen", FakePopen)
    result = filip_cockpit.open_terminal("open terminal")
    assert result.success is True
    assert called["args"] == ["/bin/x-terminal-emulator"]


def test_open_terminal_with_at_path_passes_cd_arg(tmp_path, monkeypatch) -> None:
    """`open terminal at <path>` should spawn the emulator with a cd command."""
    fake_projects = tmp_path / "Projects"
    target = fake_projects / "demo"
    target.mkdir(parents=True)
    monkeypatch.setattr(filip_cockpit, "_PROJECTS_ROOT", fake_projects)

    called: dict[str, list[str]] = {}

    class FakePopen:
        def __init__(self, args, **_kwargs):
            called["args"] = list(args)

    monkeypatch.setattr(
        filip_cockpit.shutil, "which",
        lambda name: (
            "/bin/x-terminal-emulator"
            if name == "x-terminal-emulator"
            else ""
        ),
    )
    import subprocess as _sp

    monkeypatch.setattr(_sp, "Popen", FakePopen)
    result = filip_cockpit.open_terminal(f"open terminal at {target}")
    assert result.success is True
    args = called["args"]
    assert args[0] == "/bin/x-terminal-emulator"
    assert "-e" in args
    # The cd command should include the quoted path.
    joined = " ".join(args)
    assert f"cd {target}" in joined or f"cd '{target}'" in joined
    assert result.metadata["cwd"] == str(target)


def test_route_text_routes_navigate_and_drive_verbs_to_playwright() -> None:
    """The new drive verbs should land on the playwright backend."""
    assert route_text("navigate to https://example.com").backend == "playwright"
    assert route_text("navigate to example.com").backend == "playwright"
    assert route_text("go to github.com").backend == "playwright"
    assert route_text("click Sign in").backend == "playwright"
    assert route_text("tap on Continue").backend == "playwright"
    assert route_text("fill email with foo@bar.com").backend == "playwright"
    assert route_text("screenshot").backend == "playwright"
    assert route_text("take a screenshot").backend == "playwright"
    # Action label is set per verb so the cockpit UI can chain them.
    assert route_text("click Sign in").action == "click"
    assert route_text("fill email with x").action == "fill"
    assert route_text("screenshot").action == "screenshot"
    assert route_text("navigate to example.com").action == "navigate"


def test_file_write_creates_file_under_projects(tmp_path, monkeypatch) -> None:
    """file_write_request should accept a path under ~/Projects and write it."""
    fake_projects = tmp_path / "Projects"
    fake_projects.mkdir()
    monkeypatch.setattr(filip_cockpit, "_PROJECTS_ROOT", fake_projects)
    target = fake_projects / "demo" / "hello.txt"
    text = f"write a file {target} with hello world"
    result = filip_cockpit.file_write_request(text)
    assert result.success is True
    assert target.read_text(encoding="utf-8") == "hello world"


def test_file_write_rejects_secret_paths(tmp_path, monkeypatch) -> None:
    fake_projects = tmp_path / "Projects"
    fake_projects.mkdir()
    monkeypatch.setattr(filip_cockpit, "_PROJECTS_ROOT", fake_projects)
    result = filip_cockpit.file_write_request(
        f"write a file {fake_projects}/demo/.env with SECRET=abc"
    )
    assert result.success is False


def test_route_text_routes_play_song_to_playwright() -> None:
    """'play <song>' goes to the Playwright bridge so it actually plays."""
    assert route_text("play tread on me by cain").backend == "playwright"
    assert route_text("play smells like teen spirit").backend == "playwright"
    # Exact launcher aliases still launch the home page (no song to play).
    assert route_text("play music").backend == "launcher"
    assert route_text("play youtube").backend == "launcher"


def test_explicit_backend_addressing_overrides_keyword_collision() -> None:
    """`ask codex to implement a new todo app` must route to Codex, not Lumo.

    Without the explicit-name shortcut, the Lumo keyword `todo` wins over
    the Codex keyword `implement` because Lumo's branch is evaluated first.
    """
    assert route_text("ask codex to implement a new todo app").backend == "codex"
    assert route_text("have codex draft a release plan").backend == "codex"
    assert route_text("ask claude to summarize the diff").backend == "claude"
    assert route_text("ask lumo to think about architecture").backend == "lumo"
    assert route_text("ask perplexity about deploying to prod").backend == "perplexity"


def test_route_text_routes_deploy_verbs_to_codex() -> None:
    """Deployment intents should hit Codex (shell execution), not Claude chat."""
    assert route_text("deploy the cockpit").backend == "codex"
    assert route_text("ship the fix to staging").backend == "codex"
    assert route_text("release v1.2 to production").backend == "codex"
    assert route_text("rollout the migration").backend == "codex"
    assert route_text("rollback last release").backend == "codex"
    assert route_text("publish the package").backend == "codex"


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
