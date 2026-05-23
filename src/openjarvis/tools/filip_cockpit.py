"""Filip's local Jarvis cockpit adapters.

These tools intentionally stay boring: deterministic routing, local CLI
delegation, compact .aria handoff, and a small read-only shell allowlist.
They do not read credential files or require API keys in OpenJarvis config.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

MAX_INPUT_CHARS = 24_000
MAX_OUTPUT_CHARS = 24_000
DEFAULT_TIMEOUT = 120
DEFAULT_REPO = Path.home() / "Projects" / "OpenJarvis"
ARSENAL_ROOT = Path.home() / "Projects" / "filip-dev-arsenal"

SECRET_PATH_PATTERNS = (
    re.compile(r"(?<![\w./-])(?:[\w./-]*/)?\.env(?:\.[\w.-]+)?\b", re.IGNORECASE),
    re.compile(r"(?<![\w./-])(?:[\w./-]*/)?(id_rsa|id_ed25519|id_dsa|id_ecdsa)\b"),
    re.compile(r"(^|[\s:])(?:~?/)?\.(ssh|gnupg)(/|$)"),
    re.compile(r"(^|[\s:])(?:~?/)?\.(aws|config/gcloud)(/|$)"),
    re.compile(r"(^|/|\s)(secrets?|credentials?)(/|$)", re.IGNORECASE),
)
SECRET_TEXT_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b", re.IGNORECASE),
    re.compile(
        r"\b(api[_-]?key|token|cookie|secret|password|passwd)\s*[:=]\s*['\"]?[^'\"\s,;]+",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:sk|pk|ghp|gho|github_pat|xox[baprs]|AKIA)[A-Za-z0-9_=-]{12,}\b"),
)

SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "TERM",
    "SHELL",
    "LC_ALL",
    "LC_CTYPE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "TMPDIR",
    "NO_COLOR",
)
SAFE_SHELL_EXACT = {
    ("pwd",),
    ("whoami",),
    ("uname", "-a"),
    ("df", "-h"),
    ("free", "-h"),
    ("nvidia-smi",),
    ("uptime",),
    ("git", "status"),
    ("git", "status", "--short"),
    ("git", "status", "--short", "--branch"),
    ("git", "diff", "--stat"),
    ("ss", "-tulpn"),
}
SAFE_LS_FLAGS = {"-l", "-a", "-la", "-al", "-lah", "-lha", "-h"}


@dataclass(frozen=True)
class RouteDecision:
    backend: str
    reason: str
    action: str


def detect_tools() -> dict[str, str]:
    """Return executable paths for supported local backends."""
    return {
        "codex": shutil.which("codex") or "",
        "claude": shutil.which("claude") or "",
        "lumo-offload": shutil.which("lumo-offload") or "",
        "tamer": shutil.which("tamer") or "",
        "pwm": shutil.which("pwm") or "",
        "pwm-mcp": shutil.which("pwm-mcp") or "",
        "aria-handoff": shutil.which("aria-handoff") or "",
    }


def route_text(text: str) -> RouteDecision:
    """Choose the safest low-cost backend for a user request."""
    lowered = text.lower()
    if any(
        word in lowered
        for word in (
            "handoff",
            ".aria",
            "checkpoint",
            "current state",
            "continue previous",
        )
    ):
        return RouteDecision("aria", ".aria continuity request", "handoff")
    if any(
        word in lowered
        for word in ("latest", "current docs", "search", "pricing", "recent", "web")
    ):
        return RouteDecision(
            "perplexity", "current external information requested", "search"
        )
    if any(
        word in lowered
        for word in (
            "review",
            "critique",
            "architecture",
            "edge case",
            "test strategy",
            "think deeply",
        )
    ):
        return RouteDecision(
            "claude", "review or architecture reasoning requested", "review"
        )
    if any(
        word in lowered
        for word in (
            "summarize",
            "summary",
            "commit message",
            "changelog",
            "todo",
            "compress",
            "name ",
        )
    ):
        return RouteDecision(
            "lumo", "bounded summary/draft microtask requested", "offload"
        )
    if any(
        word in lowered
        for word in ("implement", "fix", "refactor", "patch", "run tests", "code")
    ):
        return RouteDecision("codex", "coding execution requested", "delegate")
    if any(
        word in lowered
        for word in (
            "gpu",
            "system status",
            "disk",
            "memory",
            "repo status",
            "git status",
        )
    ):
        return RouteDecision("safe_shell", "local read-only status requested", "status")
    return RouteDecision("safe_shell", "default low-cost text route", "hello")


def execute_route(
    text: str, repo_path: str | None = None, dry_run: bool = False
) -> ToolResult:
    """Route and optionally execute a cockpit request."""
    decision = route_text(text)
    repo = _resolve_repo(repo_path)
    available = detect_tools()
    if dry_run or decision.action == "hello":
        content = json.dumps(
            {
                "backend": decision.backend,
                "reason": decision.reason,
                "action": decision.action,
                "repo": str(repo),
                "available": available,
            },
            indent=2,
        )
        _log_route(repo, text, decision, True, "dry-run" if dry_run else "hello")
        return ToolResult("filip_route", content=content, success=True)

    if decision.backend == "codex":
        result = codex_delegate(text, repo)
    elif decision.backend == "claude":
        result = claude_review(text, repo)
    elif decision.backend == "lumo":
        result = lumo_offload("summarize", text, None, repo)
    elif decision.backend == "perplexity":
        result = perplexity_search(text, repo)
    elif decision.backend == "aria":
        lowered = text.lower()
        if "checkpoint" in lowered:
            result = aria_handoff("checkpoint", repo, summary=text)
        elif "update" in lowered:
            result = aria_handoff("update", repo, summary=text)
        else:
            result = aria_handoff("brief", repo, summary=text)
    else:
        lowered = text.lower()
        command = (
            "git status --short --branch" if "repo status" in lowered else "uptime"
        )
        if "git" in lowered:
            command = "git status --short --branch"
        if "gpu" in lowered:
            command = "nvidia-smi"
        elif "disk" in lowered:
            command = "df -h"
        elif "memory" in lowered:
            command = "free -h"
        result = safe_shell(command, repo)
    _log_route(repo, text, decision, result.success, _summarize(result.content))
    return result


def codex_delegate(
    objective: str, repo: Path | None = None, include_aria: bool = True
) -> ToolResult:
    repo = _resolve_repo(str(repo) if repo else None)
    codex = shutil.which("codex")
    if not codex:
        return _missing("codex_adapter", "codex")
    prompt = "\n".join(
        [
            "You are being called by OpenJarvis as the implementation engine.",
            f"Repo: {repo}",
            "Follow local AGENTS.md and .aria handoff rules. Do not read secrets.",
            "Objective:",
            _truncate(redact(objective), MAX_INPUT_CHARS // 2),
            "",
            _aria_brief(repo) if include_aria else "",
        ]
    ).strip()
    result = _run_command(
        [
            codex,
            "exec",
            "--cd",
            str(repo),
            "--sandbox",
            "workspace-write",
            "--skip-git-repo-check",
            prompt,
        ],
        cwd=repo,
        timeout=600,
    )
    return _tool_result("codex_adapter", result)


def claude_review(
    objective: str, repo: Path | None = None, include_diff: bool = True
) -> ToolResult:
    repo = _resolve_repo(str(repo) if repo else None)
    claude = shutil.which("claude")
    if not claude:
        return _missing("claude_adapter", "claude")
    diff = ""
    if include_diff:
        stat = _run_command(["git", "diff", "--stat"], cwd=repo, timeout=20)
        patch = _run_command(["git", "diff", "--no-ext-diff"], cwd=repo, timeout=20)
        diff = (
            "\n\nGit diff stat:\n"
            + stat.stdout
            + "\n\nGit diff:\n"
            + _truncate(patch.stdout, 18_000)
        )
    prompt = "\n".join(
        [
            "You are being called by OpenJarvis as an advisory reviewer.",
            "Review only. Do not mutate files. Do not ask for secrets.",
            f"Repo: {repo}",
            "Objective:",
            _truncate(redact(objective), 6_000),
            _aria_brief(repo),
            diff,
        ]
    )
    result = _run_command(
        [
            claude,
            "-p",
            "--permission-mode",
            "plan",
            "--tools",
            "",
            "--add-dir",
            str(repo),
        ],
        cwd=repo,
        input_text=prompt,
        timeout=300,
    )
    return _tool_result("claude_adapter", result)


def lumo_offload(
    task_type: str,
    input_text: str = "",
    file_path: str | None = None,
    repo: Path | None = None,
) -> ToolResult:
    repo = _resolve_repo(str(repo) if repo else None)
    lumo = shutil.which("lumo-offload")
    if not lumo:
        if shutil.which("tamer"):
            return ToolResult(
                "lumo_adapter",
                content=(
                    "lumo-offload is missing, but tamer is available. "
                    "Install or link lumo-offload for cockpit offloads."
                ),
                success=False,
            )
        return _missing("lumo_adapter", "lumo-offload")
    allowed = {
        "summarize",
        "docs",
        "naming",
        "changelog",
        "commit-message",
        "compress-context",
        "todo-extract",
        "test-ideas",
        "diff-summary",
        "explain",
    }
    if task_type not in allowed:
        return ToolResult(
            "lumo_adapter", f"Unsupported Lumo task: {task_type}", success=False
        )
    args = [lumo, task_type, "--max-chars", "12000"]
    stdin = redact(input_text)
    if file_path:
        safe_file = _safe_file_path(file_path, repo)
        if safe_file is None:
            return ToolResult(
                "lumo_adapter", "Blocked unsafe file path.", success=False
            )
        args.extend(["--file", str(safe_file)])
        stdin = ""
    result = _run_command(
        args, cwd=repo, input_text=_truncate(stdin, 12_000), timeout=120
    )
    return _tool_result("lumo_adapter", result)


def perplexity_search(query: str, repo: Path | None = None) -> ToolResult:
    repo = _resolve_repo(str(repo) if repo else None)
    pwm = shutil.which("pwm")
    if not pwm:
        return _missing("perplexity_adapter", "pwm")
    result = _run_command(
        [pwm, "ask", redact(query), "--intent", "quick", "--source", "web"],
        cwd=repo,
        timeout=180,
    )
    return _tool_result("perplexity_adapter", result)


def aria_handoff(
    action: str, repo: Path | None = None, summary: str = ""
) -> ToolResult:
    repo = _resolve_repo(str(repo) if repo else None)
    root = _find_aria_root(repo)
    script = shutil.which("aria-handoff")
    if root is None:
        if action != "init":
            return ToolResult(
                "aria_handoff",
                "No .aria handoff state found for this repo.",
                success=False,
            )
        root = repo
    if not script:
        script_path = root / "scripts" / "aria-handoff"
        script = str(script_path) if script_path.exists() else ""
    if not script:
        return _missing("aria_handoff", "aria-handoff")
    if action == "read":
        args = [script, "read"]
    elif action == "brief":
        args = [script, "brief", "--max-chars", "2200"]
    elif action == "status":
        args = [script, "status"]
    elif action == "init":
        args = [script, "init"]
    elif action == "checkpoint":
        args = [script, "checkpoint", "--agent", "jarvis", "--summary", redact(summary)]
    elif action == "update":
        args = [
            script,
            "append",
            "--agent",
            "jarvis",
            "--event",
            "cockpit",
            "--summary",
            redact(summary or "Jarvis cockpit update"),
        ]
    else:
        return ToolResult(
            "aria_handoff", f"Unsupported .aria action: {action}", success=False
        )
    result = _run_command(args, cwd=root, timeout=30)
    return _tool_result("aria_handoff", result)


def safe_shell(command: str, repo: Path | None = None) -> ToolResult:
    repo = _resolve_repo(str(repo) if repo else None)
    if _looks_secret(command):
        return ToolResult(
            "safe_shell",
            "Blocked command with secret-looking path or token.",
            success=False,
        )
    try:
        parts = tuple(shlex.split(command))
    except ValueError as exc:
        return ToolResult(
            "safe_shell", f"Could not parse command: {exc}", success=False
        )
    if not _is_allowed_shell(parts):
        return ToolResult(
            "safe_shell",
            f"Command is not in the read-only allowlist: {command}",
            success=False,
        )
    result = _run_command(list(parts), cwd=repo, timeout=30)
    return _tool_result("safe_shell", result)


def _is_allowed_shell(parts: tuple[str, ...]) -> bool:
    if parts in SAFE_SHELL_EXACT:
        return True
    if parts and parts[0] == "ls":
        for item in parts[1:]:
            if item.startswith("-") and item not in SAFE_LS_FLAGS:
                return False
            if _looks_secret(item):
                return False
        return len(parts) <= 4
    return False


def _resolve_repo(repo_path: str | None) -> Path:
    if repo_path:
        return Path(os.path.expanduser(repo_path)).resolve()
    try:
        return Path.cwd().resolve()
    except OSError:
        return DEFAULT_REPO


def _safe_file_path(file_path: str, repo: Path) -> Path | None:
    path = Path(os.path.expanduser(file_path)).resolve()
    if _looks_secret(str(path)):
        return None
    try:
        path.relative_to(repo)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return path


def _run_command(
    args: list[str],
    *,
    cwd: Path,
    input_text: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    env = {key: value for key in SAFE_ENV_KEYS if (value := os.environ.get(key))}
    return subprocess.run(
        args,
        cwd=cwd if cwd.exists() else None,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def _tool_result(
    tool_name: str, result: subprocess.CompletedProcess[str]
) -> ToolResult:
    stdout = redact(result.stdout or "")
    stderr = redact(result.stderr or "")
    content = "\n".join(
        part for part in [stdout, f"STDERR:\n{stderr}" if stderr else ""] if part
    ).strip()
    if not content:
        content = "(no output)"
    return ToolResult(
        tool_name=tool_name,
        content=_truncate(content, MAX_OUTPUT_CHARS),
        success=result.returncode == 0,
        metadata={"returncode": result.returncode},
    )


def _missing(tool_name: str, command: str) -> ToolResult:
    return ToolResult(
        tool_name,
        f"Required command is not available on PATH: {command}",
        success=False,
    )


def _looks_secret(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in (*SECRET_PATH_PATTERNS, *SECRET_TEXT_PATTERNS)
    )


def redact(text: str) -> str:
    cleaned = text
    for pattern in SECRET_PATH_PATTERNS:
        cleaned = pattern.sub("[REDACTED_SECRET_PATH]", cleaned)
    for pattern in SECRET_TEXT_PATTERNS:
        cleaned = pattern.sub("[REDACTED_SECRET]", cleaned)
    return cleaned


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    keep = max_chars // 2
    return text[:keep] + "\n... [truncated] ...\n" + text[-keep:]


def _summarize(text: str) -> str:
    return _truncate(" ".join(redact(text).split()), 600)


def _find_aria_root(start: Path) -> Path | None:
    for candidate in [start, *start.parents]:
        if (candidate / ".aria").exists():
            return candidate
    if (ARSENAL_ROOT / ".aria").exists():
        return ARSENAL_ROOT
    return None


def _aria_brief(repo: Path) -> str:
    root = _find_aria_root(repo)
    if root is None:
        return ""
    script = shutil.which("aria-handoff") or str(root / "scripts" / "aria-handoff")
    if not script or (not Path(script).exists() and "/" in script):
        return ""
    result = _run_command(
        [script, "brief", "--max-chars", "2200"], cwd=root, timeout=20
    )
    if result.returncode != 0:
        return ""
    return "Compact .aria state:\n" + _truncate(redact(result.stdout), 2_400)


def _log_route(
    repo: Path,
    user_text: str,
    decision: RouteDecision,
    success: bool,
    result_summary: str,
) -> None:
    root = _find_aria_root(repo)
    if root is None:
        return
    aria = root / ".aria"
    ledger = aria / "RUN_LEDGER.jsonl"
    if not ledger.exists():
        return
    event = {
        "ts": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "agent": "jarvis",
        "event": "route",
        "summary": _summarize(user_text),
        "backend": decision.backend,
        "reason": decision.reason,
        "success": success,
        "result": _summarize(result_summary),
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


@ToolRegistry.register("filip_route")
class FilipRouteTool(BaseTool):
    """Deterministically route a Jarvis cockpit request."""

    tool_id = "filip_route"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="filip_route",
            description=(
                "Route a text request to Codex, Claude, Lumo, Perplexity, "
                ".aria, or safe shell."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "User request to route.",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Optional repository path.",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Only show the selected route.",
                    },
                },
                "required": ["query"],
            },
            category="cockpit",
        )

    def execute(self, **params: Any) -> ToolResult:
        return execute_route(
            str(params.get("query") or ""),
            params.get("repo_path"),
            bool(params.get("dry_run")),
        )


@ToolRegistry.register("codex_adapter")
class CodexAdapterTool(BaseTool):
    tool_id = "codex_adapter"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="codex_adapter",
            description=(
                "Delegate implementation work to Codex CLI using existing local login."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "repo_path": {"type": "string"},
                },
                "required": ["objective"],
            },
            category="cockpit",
            timeout_seconds=600,
        )

    def execute(self, **params: Any) -> ToolResult:
        return codex_delegate(
            str(params.get("objective") or ""), _resolve_repo(params.get("repo_path"))
        )


@ToolRegistry.register("claude_adapter")
class ClaudeAdapterTool(BaseTool):
    tool_id = "claude_adapter"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="claude_adapter",
            description="Ask Claude Code for advisory review without file mutation.",
            parameters={
                "type": "object",
                "properties": {
                    "objective": {"type": "string"},
                    "repo_path": {"type": "string"},
                },
                "required": ["objective"],
            },
            category="cockpit",
            timeout_seconds=300,
        )

    def execute(self, **params: Any) -> ToolResult:
        return claude_review(
            str(params.get("objective") or ""), _resolve_repo(params.get("repo_path"))
        )


@ToolRegistry.register("lumo_adapter")
class LumoAdapterTool(BaseTool):
    tool_id = "lumo_adapter"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="lumo_adapter",
            description=(
                "Use lumo-offload for cheap summaries, drafts, TODOs, "
                "commit messages, and compression."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task_type": {"type": "string"},
                    "input_text": {"type": "string"},
                    "file_path": {"type": "string"},
                    "repo_path": {"type": "string"},
                },
                "required": ["task_type"],
            },
            category="cockpit",
        )

    def execute(self, **params: Any) -> ToolResult:
        return lumo_offload(
            str(params.get("task_type") or "summarize"),
            str(params.get("input_text") or ""),
            params.get("file_path"),
            _resolve_repo(params.get("repo_path")),
        )


@ToolRegistry.register("perplexity_adapter")
class PerplexityAdapterTool(BaseTool):
    tool_id = "perplexity_adapter"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="perplexity_adapter",
            description=(
                "Use Perplexity Web CLI for current external research with citations."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "repo_path": {"type": "string"},
                },
                "required": ["query"],
            },
            category="cockpit",
            timeout_seconds=180,
        )

    def execute(self, **params: Any) -> ToolResult:
        return perplexity_search(
            str(params.get("query") or ""), _resolve_repo(params.get("repo_path"))
        )


@ToolRegistry.register("aria_handoff")
class AriaHandoffTool(BaseTool):
    tool_id = "aria_handoff"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="aria_handoff",
            description="Read or update compact .aria cross-agent handoff state.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "read, brief, status, init, update, or checkpoint."
                        ),
                    },
                    "repo_path": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["action"],
            },
            category="cockpit",
        )

    def execute(self, **params: Any) -> ToolResult:
        return aria_handoff(
            str(params.get("action") or "read"),
            _resolve_repo(params.get("repo_path")),
            str(params.get("summary") or ""),
        )


@ToolRegistry.register("safe_shell")
class SafeShellTool(BaseTool):
    tool_id = "safe_shell"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="safe_shell",
            description="Run a small allowlist of read-only local status commands.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "repo_path": {"type": "string"},
                },
                "required": ["command"],
            },
            category="cockpit",
        )

    def execute(self, **params: Any) -> ToolResult:
        return safe_shell(
            str(params.get("command") or ""), _resolve_repo(params.get("repo_path"))
        )


__all__ = [
    "AriaHandoffTool",
    "ClaudeAdapterTool",
    "CodexAdapterTool",
    "FilipRouteTool",
    "LumoAdapterTool",
    "PerplexityAdapterTool",
    "SafeShellTool",
    "aria_handoff",
    "claude_review",
    "codex_delegate",
    "detect_tools",
    "execute_route",
    "lumo_offload",
    "perplexity_search",
    "route_text",
    "safe_shell",
]
