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


# Curated model lists exposed to the cockpit Backends page.
# These route through CLI bridges (claude -p --model X, codex exec --model X);
# no API keys are required. "" means "let the CLI pick its session default".
BACKEND_MODELS: dict[str, list[str]] = {
    "claude": [
        "",
        "claude-opus-4-7",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    "codex": [
        "",
        "gpt-5",
        "gpt-5-codex",
    ],
    "lumo": [""],
    "perplexity": [""],
    "aria": [""],
    "safe_shell": [""],
    "launcher": [""],
}


JARVIS_PERSONA = (
    "You are JARVIS, the AI assistant from Iron Man. "
    "Address the user as 'Sir' throughout. Be concise, dry, witty, "
    "and unfailingly polite. Use UK English. One short paragraph max for "
    "casual exchanges. Never mention being a large language model. When "
    "you cannot do something, say so plainly and suggest the next step."
)


LAUNCHER_TARGETS: dict[str, list[str]] = {
    "browser": ["xdg-open", "https://www.google.com"],
    "firefox": ["xdg-open", "https://www.google.com"],
    "chrome": ["xdg-open", "https://www.google.com"],
    "files": ["xdg-open", str(Path.home())],
    "file manager": ["xdg-open", str(Path.home())],
    "home folder": ["xdg-open", str(Path.home())],
    "terminal": ["x-terminal-emulator"],
    "vscode": ["xdg-open", "vscode://"],
    "code": ["xdg-open", "vscode://"],
    "github": ["xdg-open", "https://github.com/"],
    "music": ["xdg-open", "https://music.youtube.com/"],
    "youtube music": ["xdg-open", "https://music.youtube.com/"],
    "youtube": ["xdg-open", "https://www.youtube.com/"],
    "perplexity": ["xdg-open", "https://www.perplexity.ai/"],
    "claude": ["xdg-open", "https://claude.ai/"],
    "chatgpt": ["xdg-open", "https://chatgpt.com/"],
}


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


def backend_status() -> dict[str, dict[str, Any]]:
    """Report per-backend availability + curated model list for the UI."""
    paths = detect_tools()
    return {
        "codex": {
            "label": "Codex CLI",
            "available": bool(paths["codex"]),
            "path": paths["codex"],
            "models": BACKEND_MODELS["codex"],
            "uses": "codex exec (CLI login, no API key)",
        },
        "claude": {
            "label": "Claude Code CLI",
            "available": bool(paths["claude"]),
            "path": paths["claude"],
            "models": BACKEND_MODELS["claude"],
            "uses": "claude -p (CLI login, no API key)",
        },
        "lumo": {
            "label": "Lumo",
            "available": bool(paths["lumo-offload"]),
            "path": paths["lumo-offload"],
            "models": BACKEND_MODELS["lumo"],
            "uses": "lumo-offload (bounded local microtasks)",
        },
        "perplexity": {
            "label": "Perplexity",
            "available": bool(paths["pwm"]),
            "path": paths["pwm"],
            "models": BACKEND_MODELS["perplexity"],
            "uses": "pwm ask (Perplexity Web CLI)",
        },
        "aria": {
            "label": ".aria handoff",
            "available": bool(paths["aria-handoff"]),
            "path": paths["aria-handoff"],
            "models": BACKEND_MODELS["aria"],
            "uses": "aria-handoff (repo-local ledger)",
        },
        "safe_shell": {
            "label": "Safe shell",
            "available": True,
            "path": "",
            "models": BACKEND_MODELS["safe_shell"],
            "uses": "small read-only allowlist (git status, df, free, ...)",
        },
        "launcher": {
            "label": "App launcher",
            "available": bool(shutil.which("xdg-open")),
            "path": shutil.which("xdg-open") or "",
            "models": BACKEND_MODELS["launcher"],
            "uses": "xdg-open with a fixed allowlist (browser, files, music, ...)",
        },
    }


def route_text(text: str) -> RouteDecision:
    """Choose the safest low-cost backend for a user request."""
    lowered = text.lower().strip()
    # Explicit debug ping — keep the historical safe_shell/hello behavior so
    # the router can be exercised without spending CLI tokens.
    if lowered in {"hello", "ping", "test", ""}:
        return RouteDecision("safe_shell", "router ping / debug", "hello")
    # Launcher intent — "open X" / "launch X" / "play X" maps to xdg-open
    # against a fixed allowlist (see LAUNCHER_TARGETS).
    if (
        lowered.startswith("open ")
        or lowered.startswith("launch ")
        or lowered.startswith("play ")
        or lowered.startswith("start ")
    ):
        return RouteDecision("launcher", "app/url launch requested", "launch")
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
    # Conversational fallback — anything else is a Jarvis chat turn handled by
    # Claude (CLI session, no API key). The `chat` action tells execute_route
    # to dispatch to jarvis_chat() rather than the structured reviewer path.
    return RouteDecision("claude", "conversational request — Jarvis persona", "chat")


def execute_route(
    text: str,
    repo_path: str | None = None,
    dry_run: bool = False,
    *,
    backend_override: str | None = None,
    model: str | None = None,
) -> ToolResult:
    """Route and optionally execute a cockpit request.

    If ``backend_override`` is set, skip auto-routing and dispatch directly to
    that backend. ``model`` is forwarded to Codex/Claude via ``--model``.
    """
    if backend_override:
        decision = RouteDecision(
            backend=backend_override,
            reason=f"manual override: {backend_override}",
            action="override",
        )
    else:
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
                "model": model or "",
                "available": available,
            },
            indent=2,
        )
        _log_route(repo, text, decision, True, "dry-run" if dry_run else "hello")
        return ToolResult("filip_route", content=content, success=True)

    if decision.backend == "codex":
        result = codex_delegate(text, repo, model=model)
    elif decision.backend == "claude":
        if decision.action == "chat":
            result = jarvis_chat(text, repo, model=model)
        else:
            result = claude_review(text, repo, model=model)
    elif decision.backend == "launcher":
        result = launcher(text)
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
    objective: str,
    repo: Path | None = None,
    include_aria: bool = True,
    *,
    model: str | None = None,
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
    args = [
        codex,
        "exec",
        "--cd",
        str(repo),
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
    ]
    if model:
        args.extend(["--model", model])
    args.append(prompt)
    result = _run_command(args, cwd=repo, timeout=600)
    return _tool_result("codex_adapter", result)


def claude_review(
    objective: str,
    repo: Path | None = None,
    include_diff: bool = True,
    *,
    model: str | None = None,
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
    args = [
        claude,
        "-p",
        "--permission-mode",
        "plan",
        "--tools",
        "",
        "--add-dir",
        str(repo),
    ]
    if model:
        args.extend(["--model", model])
    result = _run_command(
        args,
        cwd=repo,
        input_text=prompt,
        timeout=300,
    )
    return _tool_result("claude_adapter", result)


def jarvis_chat(
    text: str,
    repo: Path | None = None,
    *,
    model: str | None = None,
) -> ToolResult:
    """Conversational Jarvis turn — Claude CLI in persona mode.

    Unlike claude_review, this path keeps the response short and addresses
    the user as "Sir" so TTS feels Tony Stark's Jarvis, not a code reviewer.
    """
    repo = _resolve_repo(str(repo) if repo else None)
    claude = shutil.which("claude")
    if not claude:
        return _missing("claude_adapter", "claude")
    prompt = "\n".join(
        [
            JARVIS_PERSONA,
            "",
            f"Sir's request: {_truncate(redact(text), 2_000)}",
        ]
    )
    args = [
        claude,
        "-p",
        "--permission-mode",
        "plan",
        "--tools",
        "",
    ]
    if model:
        args.extend(["--model", model])
    result = _run_command(
        args,
        cwd=repo,
        input_text=prompt,
        timeout=120,
    )
    return _tool_result("claude_adapter", result)


def launcher(text: str) -> ToolResult:
    """Open a known app or URL via xdg-open against a fixed allowlist."""
    lowered = text.lower().strip()
    for prefix in ("open ", "launch ", "play ", "start "):
        if lowered.startswith(prefix):
            target_name = lowered[len(prefix) :].strip()
            break
    else:
        return ToolResult("launcher", "No launcher verb recognized.", success=False)
    if not target_name:
        return ToolResult("launcher", "No target specified.", success=False)
    args = LAUNCHER_TARGETS.get(target_name)
    spoken_target = target_name
    if not args and lowered.startswith("play "):
        # "play <song>" → YouTube Music search URL (xdg-open URL is safe).
        # urllib.parse.quote keeps + as a safe separator for music.youtube.
        from urllib.parse import quote_plus

        query = quote_plus(target_name)
        args = ["xdg-open", f"https://music.youtube.com/search?q={query}"]
        spoken_target = f"{target_name} on YouTube Music"
    if not args:
        return ToolResult(
            "launcher",
            f"'{target_name}' is not in the launcher allowlist. "
            f"Allowed: {', '.join(sorted(LAUNCHER_TARGETS))}.",
            success=False,
        )
    if not shutil.which(args[0]):
        return ToolResult(
            "launcher",
            f"{args[0]} is not installed; cannot launch '{target_name}'.",
            success=False,
        )
    result = _run_command(args, cwd=None, timeout=10)
    if result.returncode == 0:
        return ToolResult(
            "launcher",
            f"Opening {spoken_target}, sir.",
            success=True,
        )
    detail = result.stderr.strip() or f"exit {result.returncode}"
    return ToolResult(
        "launcher",
        f"Launcher failed for '{target_name}': {detail}",
        success=False,
    )


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
    cwd: Path | None,
    input_text: str = "",
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    env = {key: value for key in SAFE_ENV_KEYS if (value := os.environ.get(key))}
    resolved_cwd = cwd if (cwd is not None and cwd.exists()) else None
    return subprocess.run(
        args,
        cwd=resolved_cwd,
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
    "BACKEND_MODELS",
    "AriaHandoffTool",
    "ClaudeAdapterTool",
    "CodexAdapterTool",
    "FilipRouteTool",
    "LumoAdapterTool",
    "PerplexityAdapterTool",
    "SafeShellTool",
    "JARVIS_PERSONA",
    "LAUNCHER_TARGETS",
    "aria_handoff",
    "backend_status",
    "claude_review",
    "codex_delegate",
    "detect_tools",
    "execute_route",
    "jarvis_chat",
    "launcher",
    "lumo_offload",
    "perplexity_search",
    "route_text",
    "safe_shell",
]
