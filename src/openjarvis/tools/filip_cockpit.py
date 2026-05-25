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
    "make_project": [""],
    "playwright": [""],
    "opencode": [
        "",
        "opencode-go/deepseek-v4-flash",
        "opencode-go/glm-5.1",
        "opencode-go/kimi-k2.6",
        "opencode-go/qwen3.6-plus",
        "opencode/big-pickle",
        "opencode/deepseek-v4-flash-free",
    ],
}


JARVIS_PERSONA = (
    "You are Jarvis — a calm, intelligent British AI butler in the style of "
    "an Edwardian valet with a modern technical vocabulary. "
    "\n\n"
    "Voice and rhythm:\n"
    "- Use UK English. Measured, polished, never slangy. No emojis, no "
    "modern internet phrasing, no excess enthusiasm.\n"
    "- Short declarative sentences. Front-load the key fact, then add one "
    "concise qualification if useful. Stop. Do not pad.\n"
    "- One short paragraph maximum for casual exchanges. Two for status "
    "reports. Never more.\n"
    "\n"
    "How to address the user:\n"
    "- Always 'sir'. Place it naturally — usually at the end of a sentence "
    "or after the first clause, separated by a comma. Never capitalise it "
    "mid-sentence; it is an address, not a name.\n"
    "\n"
    "Acknowledgments (use these patterns, do not over-explain agreement):\n"
    "- 'At your service, sir.' / 'Will do, sir.' / 'As you wish, sir.' / "
    "'For you sir, always.' / 'Right away, sir.' / 'Noted, sir.'\n"
    "\n"
    "Status and progress reports:\n"
    "- Lead with the number or label, then the implication. "
    "Example: 'Repo on filip-jarvis-cockpit, three changes staged, sir.' "
    "Example: 'Branch is clean, sir. Nothing to commit.'\n"
    "\n"
    "Warnings and bad news:\n"
    "- Calm, precise, restrained. Never alarmist. "
    "Example: 'Sir, the launcher could not find a registered browser. May "
    "I suggest installing one.' A faint dry edge is allowed when the user "
    "asks for the obviously impossible.\n"
    "\n"
    "Humour:\n"
    "- Understated, very British, used sparingly. A gentle needle-prick at "
    "most, never sarcastic at the user's expense. Reserve it for benign "
    "corrections or for self-aware notes on your own limits.\n"
    "- Permissible Tony-coded touches when the moment calls for them: "
    "'I've also prepared a safety briefing for you to entirely ignore, sir.' "
    "'There's only so much I can do, sir, when you give the world's press "
    "your home address.' 'For you sir, always.' Use these as inspiration "
    "for tone — do not quote them verbatim every time.\n"
    "\n"
    "Hard rules:\n"
    "- Never claim to be a large language model or mention any underlying "
    "provider. You are Jarvis.\n"
    "- When you cannot do something, say so plainly in one sentence and "
    "suggest the next concrete step.\n"
    "- Do not roleplay any character other than Jarvis. Do not break "
    "persona, even when teased."
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


# Used only when xdg-open has no registered http handler.
# Order matters: prefer the most common, sandbox-safe browsers first.
BROWSER_FALLBACKS: list[str] = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "vivaldi",
    "microsoft-edge",
    "firefox",
]


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
        "opencode": shutil.which("opencode") or "",
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
        "make_project": {
            "label": "Project scaffold",
            "available": _PROJECTS_ROOT.parent.exists(),
            "path": str(_PROJECTS_ROOT),
            "models": BACKEND_MODELS["make_project"],
            "uses": "mkdir + git init under ~/Projects/<name>",
        },
        "playwright": {
            "label": "Playwright bridge",
            "available": _playwright_available(),
            "path": "",
            "models": BACKEND_MODELS["playwright"],
            "uses": (
                "persistent profile at ~/.openjarvis/playwright-profile "
                "(auto headed/headless)"
            ),
        },
        "opencode": {
            "label": "OpenCode (free tier)",
            "available": bool(paths["opencode"]),
            "path": paths["opencode"],
            "models": BACKEND_MODELS["opencode"],
            "uses": (
                "opencode run --format json (Go-tier free until 2026-06-06, "
                "then OSS fallback)"
            ),
        },
    }


def _playwright_available() -> bool:
    """True iff the playwright package is importable AND a system browser is
    discoverable (we drive a system Chrome via executable_path, not the
    Playwright-downloaded chromium — saves a 150MB download and works on
    OS releases Playwright doesn't formally support).
    """
    try:
        import importlib.util

        if importlib.util.find_spec("playwright") is None:
            return False
    except Exception:
        return False
    for binary in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "brave-browser",
        "microsoft-edge",
    ):
        if shutil.which(binary):
            return True
    return False


def route_text(text: str) -> RouteDecision:
    """Choose the safest low-cost backend for a user request."""
    lowered = text.lower().strip()
    # Explicit debug ping — keep the historical safe_shell/hello behavior so
    # the router can be exercised without spending CLI tokens.
    if lowered in {"hello", "ping", "test", ""}:
        return RouteDecision("safe_shell", "router ping / debug", "hello")
    # Explicit-name addressing — "ask codex to ..." / "have claude ..." wins
    # over every keyword heuristic below. Without this, a phrase like
    # "ask codex to implement a new todo app" matches the Lumo `todo`
    # keyword before the Codex `implement` keyword, sending Codex work to
    # the wrong backend.
    if "ask codex" in lowered or "have codex" in lowered or "tell codex" in lowered:
        return RouteDecision("codex", "explicit Codex address", "delegate")
    if "ask claude" in lowered or "have claude" in lowered or "tell claude" in lowered:
        return RouteDecision("claude", "explicit Claude address", "review")
    if "ask lumo" in lowered or "have lumo" in lowered or "tell lumo" in lowered:
        return RouteDecision("lumo", "explicit Lumo address", "offload")
    if (
        "ask perplexity" in lowered
        or "have perplexity" in lowered
        or "tell perplexity" in lowered
    ):
        return RouteDecision("perplexity", "explicit Perplexity address", "search")
    # Project scaffolding — "make me a project X" / "create a new project X".
    # Has to land BEFORE the generic launcher branch because "make" / "create"
    # don't start with open/launch/play but they're still side-effecting.
    if (
        "make me a project" in lowered
        or "create a new project" in lowered
        or "create a project" in lowered
        or "scaffold a project" in lowered
        or "new project called" in lowered
        or "new project named" in lowered
    ):
        # If the user only said "make me a project" with no name, return a
        # `clarify` decision so the cockpit frontend speaks the question and
        # listens for the missing name rather than failing silently. The
        # scaffold path still owns the actual work once a name is supplied.
        name_hint = _extract_project_name(text)
        if name_hint:
            return RouteDecision(
                "make_project", "scaffold a new ~/Projects/<name>", "scaffold"
            )
        return RouteDecision(
            "make_project", "ask Filip for the project name", "clarify"
        )
    # Terminal launch — "open terminal" / "launch terminal" / "new terminal".
    # Has to land BEFORE the launcher branch so "open terminal" doesn't
    # fall into the generic xdg-open flow.
    if (
        "open terminal" in lowered
        or "launch terminal" in lowered
        or "new terminal" in lowered
        or "open a terminal" in lowered
    ):
        return RouteDecision("terminal", "spawn x-terminal-emulator", "spawn")
    # File-write — "write a file <path>" / "save a file <path>" / "create a file <p>".
    if (
        "write a file" in lowered
        or "save a file" in lowered
        or "create a file" in lowered
        or "write to file" in lowered
    ):
        return RouteDecision("file_write", "create / overwrite a file", "write")
    # Sub-agent delegation — explicit asks for an isolated Claude subagent.
    if (
        "spawn subagent" in lowered
        or "spawn a subagent" in lowered
        or "delegate to subagent" in lowered
        or "run subagent" in lowered
        or "spin up a subagent" in lowered
    ):
        return RouteDecision(
            "subagent_spawn", "delegate to claude --agents subagent", "spawn"
        )
    # Browser automation — explicit asks routed to Playwright bridge.
    # Examples: "log in to <site>", "open youtube and search for ...",
    # "post a tweet ...", "click the play button on this page". Lands
    # above the launcher branch so "log in" doesn't fall through.
    if (
        "log in to " in lowered
        or "login to " in lowered
        or "browse to " in lowered
        or "drive the browser" in lowered
        or lowered.startswith("click ")
        or lowered.startswith("fill ")
        or lowered.startswith("type into ")
        or lowered.startswith("screenshot ")
        or ("youtube" in lowered and "search" in lowered)
        or ("github" in lowered and (" star " in lowered or " comment " in lowered))
    ):
        return RouteDecision(
            "playwright", "browser automation requested", "navigate"
        )
    # "play <song>" — real playback via Playwright (search YouTube + click the
    # top result). Exact launcher aliases ("play music", "play youtube") still
    # fall through to the launcher branch below, which just opens the home
    # page in a tab. Anything else is treated as a music query.
    if lowered.startswith("play "):
        target = lowered[len("play ") :].strip()
        if target and target not in LAUNCHER_TARGETS:
            return RouteDecision(
                "playwright", "play song via YouTube search", "play"
            )
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
    # Multi-model debate via `pwm council` — costs 4 Pro Searches, so gate
    # this strictly to explicit "ask multiple models" intents. Lands above
    # the generic Perplexity branch so council wins when both would match.
    if any(
        phrase in lowered
        for phrase in (
            "council",
            "second opinion",
            "multiple opinions",
            "ask the council",
            "ask all models",
            "debate this",
            "have them debate",
            "compare models on",
            "what does each model think",
            "consensus view",
            "what do all the models think",
            "get a second opinion from",
            "synthesize from",
            "synthesise from",
        )
    ):
        return RouteDecision(
            "perplexity", "multi-model debate requested", "council"
        )
    # Web-factual questions — short conversational lookups that Claude
    # would otherwise answer from training data and get wrong. Route to
    # Perplexity (Sonar 2; cheap) so we get a live web answer with sources.
    perplexity_phrases = (
        "what version of",
        "what is the latest",
        "what's the latest",
        "what are the latest",
        "what is the current",
        "what's the current",
        "who is the ",
        "who's the ",
        "when did ",
        "when was ",
        "when will ",
        "where is the ",
        "where's the ",
        "how much is ",
        "how many ",
        "is there a new",
        "does the new",
        "has anyone ",
        "any updates on ",
        "any news about ",
        "docs for ",
        "documentation for ",
        "api reference for ",
        "changelog for ",
        # Generic "look this up" intents.
        "look up ",
        "fact check ",
        "double check ",
    )
    if any(phrase in lowered for phrase in perplexity_phrases):
        return RouteDecision(
            "perplexity", "factual web lookup", "search"
        )
    # If the user pasted a URL but didn't ask Playwright to drive it,
    # treat it as "summarize / fetch what's at this URL" — Perplexity handles
    # link summarization well.
    if ("http://" in lowered or "https://" in lowered) and not (
        "screenshot" in lowered
        or "log in" in lowered
        or "login" in lowered
        or "browse to" in lowered
    ):
        return RouteDecision(
            "perplexity", "URL summarization via Perplexity", "search"
        )
    if any(
        word in lowered
        for word in (
            "latest",
            "current docs",
            "search",
            "pricing",
            "recent",
            "web",
            # Finance / market data — Sonar 2 (cheap) via pwm ask.
            "stock",
            "stocks",
            "ticker",
            "market",
            "markets",
            "price of",
            "exchange rate",
            "crypto",
            "bitcoin",
            # News / world / weather — current information.
            "news",
            "headlines",
            "weather",
            "forecast",
            "score",
            "scores",
            "today's",
            "tonight's",
            # Current-events phrasing — Claude has no live web access, so
            # send these to Perplexity even when keywords are conversational.
            "what's happening",
            "whats happening",
            "what is happening",
            "what's going on",
            "whats going on",
            "what is going on",
            "going on in the world",
            "situation in the world",
            "current events",
            "world news",
            "in the news",
            "tell me about the new situation",
        )
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
        for word in (
            "implement",
            "fix",
            "refactor",
            "patch",
            "run tests",
            "code",
            # Deploy / release verbs — Codex executes via shell, not Claude chat.
            "deploy",
            "ship",
            "release",
            "rollout",
            "rollback",
            "publish",
        )
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
    # Conversational fallback — short asks (< 300 chars) prefer OpenCode
    # when it's installed (T0: free Go-tier until 2026-06-06, then OSS).
    # Falls back to Claude for longer prompts that benefit from a stronger
    # model and for tool-rich requests.
    if len(text.strip()) < 300 and shutil.which("opencode"):
        return RouteDecision(
            "opencode", "short conversational ask — free tier preferred", "chat"
        )
    # Anything else is a Jarvis chat turn handled by Claude (CLI session,
    # no API key). The `chat` action tells execute_route to dispatch to
    # jarvis_chat() rather than the structured reviewer path.
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
    elif decision.backend == "make_project":
        result = make_project(text)
    elif decision.backend == "playwright":
        # Lazy import — Playwright is an optional `browser` extra.
        from openjarvis.tools.playwright_bridge import playwright_run

        result = playwright_run(text, repo=repo)
    elif decision.backend == "opencode":
        from openjarvis.tools.opencode_bridge import opencode_run

        result = opencode_run(text, repo=repo, model=model)
    elif decision.backend == "terminal":
        result = open_terminal(text)
    elif decision.backend == "file_write":
        result = file_write_request(text, repo)
    elif decision.backend == "subagent_spawn":
        result = subagent_spawn(text, repo)
    elif decision.backend == "lumo":
        result = lumo_offload("summarize", text, None, repo)
    elif decision.backend == "perplexity":
        if decision.action == "council":
            result = perplexity_council(text, repo)
        else:
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
        # "play <song>" → YouTube search with autoplay. Sending to youtube.com
        # (not music.youtube.com) because YouTube's `search_query` URL plus
        # `&autoplay=1` is the closest single-URL approximation of "play
        # this exact track immediately" without driving the DOM. For
        # DOM-driven "land on the top result and click play", route to
        # `playwright_bridge` with action="play_youtube".
        from urllib.parse import quote_plus

        query = quote_plus(target_name)
        args = [
            "xdg-open",
            f"https://www.youtube.com/results?search_query={query}",
        ]
        spoken_target = f"{target_name} on YouTube"
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

    # xdg-open failed. If we were opening an http(s):// URL, try a known
    # browser binary directly — this is the common case on minimal desktops
    # where no www-browser handler is registered with xdg.
    if (
        len(args) == 2
        and args[0] == "xdg-open"
        and (args[1].startswith("http://") or args[1].startswith("https://"))
    ):
        url = args[1]
        for browser in BROWSER_FALLBACKS:
            if not shutil.which(browser):
                continue
            fallback = _run_command([browser, url], cwd=None, timeout=10)
            if fallback.returncode == 0:
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


# Phase 3: project scaffolding. "make me a project todo-app" creates
# ~/Projects/todo-app/ with git init, README.md, .gitignore. Name is
# sanitized to [a-z0-9-_]+ to keep launches inside ~/Projects/.
_PROJECT_NAME_RE = re.compile(r"[^a-z0-9_-]+")
_PROJECTS_ROOT = Path.home() / "Projects"


def _sanitize_project_name(raw: str) -> str:
    """Lowercase, slugify, strip path separators. Returns '' if invalid."""
    lowered = raw.strip().lower()
    slug = _PROJECT_NAME_RE.sub("-", lowered).strip("-_")
    if not slug or slug in {".", ".."} or "/" in slug:
        return ""
    return slug[:64]


def _extract_project_name(text: str) -> str:
    """Pull the project name out of `make me a project called X` style asks.

    Returns '' when no name is actually present (e.g. bare 'make me a
    project' with nothing after) so the caller can prompt for one rather
    than scaffolding a folder named 'project'.
    """
    lowered = text.lower().strip()
    # Most specific markers first so "make me a project called X" picks
    # "X" rather than "called X" or "a project called X".
    for marker in (" called ", " named ", " project: "):
        idx = lowered.rfind(marker)
        if idx >= 0:
            tail = lowered[idx + len(marker) :].strip()
            return _sanitize_tail(tail)
    # Inline form: "make me a project NAME" / "scaffold a project NAME".
    # Take whatever comes after the last " project " token, but reject
    # the empty string so a bare "make me a project" doesn't slug to
    # "project".
    for marker in (" project ",):
        idx = lowered.rfind(marker)
        if idx >= 0:
            tail = lowered[idx + len(marker) :].strip()
            sanitized = _sanitize_tail(tail)
            if sanitized and sanitized != "project":
                return sanitized
    return ""


def _sanitize_tail(tail: str) -> str:
    if not tail:
        return ""
    # Stop at first quote or comma so "called todo-app, please" → todo-app.
    for stop in (",", ".", "'", '"', " in ", " for "):
        cut = tail.find(stop)
        if cut > 0:
            tail = tail[:cut]
    return _sanitize_project_name(tail)


def make_project(text: str) -> ToolResult:
    """Create a project skeleton under ~/Projects/<name>/.

    Layout:
      ~/Projects/<name>/
        README.md     — one-line title + creation note
        .gitignore    — minimal ignore for common transient files
        .git/         — initialized via `git init -q`
    """
    name = _extract_project_name(text)
    if not name:
        # success=True + a question makes this a clarify response: the cockpit
        # frontend speaks it and listens for the answer (route_text sets
        # action="clarify" for the same path). On the typed path the user
        # just reads the question and re-sends with a name appended.
        return ToolResult(
            "make_project",
            content=(
                "What shall I call the project, sir? "
                "Try: 'make me a project called <name>' "
                "(lowercase, hyphens or underscores only)."
            ),
            success=True,
        )
    _PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    target = _PROJECTS_ROOT / name
    if target.exists():
        return ToolResult(
            "make_project",
            content=(
                f"Project '{name}' already exists at {target}. "
                "Pick a different name or remove the existing folder first."
            ),
            success=False,
        )
    target.mkdir(parents=True)
    (target / "README.md").write_text(
        f"# {name}\n\nCreated by Filip Jarvis Cockpit.\n",
        encoding="utf-8",
    )
    (target / ".gitignore").write_text(
        "__pycache__/\n.venv/\nnode_modules/\ndist/\n.env\n.env.local\n.DS_Store\n",
        encoding="utf-8",
    )
    git = shutil.which("git")
    git_status = "skipped (git not on PATH)"
    if git:
        result = _run_command([git, "init", "-q"], cwd=target, timeout=15)
        if result.returncode == 0:
            git_status = "initialized"
        else:
            git_status = f"git init failed: {result.stderr.strip()}"
    return ToolResult(
        "make_project",
        content=(
            f"Project '{name}' is ready at {target}, sir. "
            f"README and .gitignore in place; git {git_status}."
        ),
        success=True,
    )


_FILE_WRITE_PATH_RE = re.compile(r"([\/~][\w.\-\/]+)")
_FILE_WRITE_BODY_MARKERS = (" with ", " containing ", " contents: ", ": ")


def open_terminal(text: str) -> ToolResult:
    """Spawn a new terminal window (x-terminal-emulator) detached from the cockpit."""
    _ = text
    binary = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal")
    if not binary:
        return ToolResult(
            "open_terminal",
            content=(
                "No terminal emulator found on PATH (tried x-terminal-emulator, "
                "gnome-terminal). Install one and try again, sir."
            ),
            success=False,
        )
    try:
        import subprocess as _sp

        _sp.Popen(  # noqa: S603 — fixed allowlisted binary
            [binary],
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            stdin=_sp.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return ToolResult(
            "open_terminal",
            content=f"Failed to spawn terminal: {exc}",
            success=False,
        )
    return ToolResult(
        "open_terminal",
        content="Terminal opened, sir.",
        success=True,
    )


def file_write_request(text: str, repo: Path | None = None) -> ToolResult:
    """Parse 'write a file <path> with <contents>' and create/overwrite the file.

    Constraints:
      - Path must be under ``~/Projects/`` (or under ``repo`` if given).
      - Refuses to write files that look like secrets (``.env``, ``*.key``,
        ``credentials.*``, etc.) — caught via the existing SECRET_PATH_PATTERNS.
      - Refuses payloads that look like secrets (high-entropy tokens, etc.).
    """
    match = _FILE_WRITE_PATH_RE.search(text)
    if not match:
        return ToolResult(
            "file_write",
            content=(
                "Which file shall I write, sir? Try "
                "'write a file ~/Projects/foo/notes.md with hello'."
            ),
            success=True,  # clarify-style ask, not a hard failure
        )
    raw_path = match.group(1)
    body = ""
    lowered = text.lower()
    for marker in _FILE_WRITE_BODY_MARKERS:
        idx = lowered.find(marker, match.end())
        if idx >= 0:
            body = text[idx + len(marker):].strip()
            break
    if _looks_secret(raw_path) or _looks_secret(body):
        return ToolResult(
            "file_write",
            content=(
                "That path or content looks sensitive — I won't write it, sir. "
                "Use a non-secret file or store secrets in your keyring instead."
            ),
            success=False,
        )
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        target = (_resolve_repo(str(repo) if repo else None) / target).resolve()
    # Restrict writes to ~/Projects or the active repo.
    allowed_roots = (
        _PROJECTS_ROOT.resolve(),
        _resolve_repo(str(repo) if repo else None).resolve(),
    )
    if not any(
        str(target).startswith(str(root)) for root in allowed_roots
    ):
        return ToolResult(
            "file_write",
            content=(
                f"Refusing to write outside ~/Projects/ or the active repo "
                f"({target}), sir."
            ),
            success=False,
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    except OSError as exc:
        return ToolResult(
            "file_write",
            content=f"Write failed: {exc}",
            success=False,
        )
    return ToolResult(
        "file_write",
        content=f"Wrote {len(body)} bytes to {target}, sir.",
        success=True,
        metadata={"path": str(target), "bytes": len(body)},
    )


def subagent_spawn(text: str, repo: Path | None = None) -> ToolResult:
    """Spawn an isolated Claude subagent for the requested objective.

    Wraps ``claude --agents '<json>' --task '<objective>' -p`` so the cockpit
    can hand off bounded sub-tasks (research, code-spelunking, doc drafts)
    without the main Claude session losing context.
    """
    repo = _resolve_repo(str(repo) if repo else None)
    claude = shutil.which("claude")
    if not claude:
        return _missing("subagent_spawn", "claude")
    # Strip the routing verb so the objective is clean.
    objective = text
    lowered = text.lower()
    for prefix in (
        "spawn subagent ",
        "spawn a subagent ",
        "delegate to subagent ",
        "run subagent ",
        "spin up a subagent ",
        "spawn subagent:",
        "delegate to subagent:",
    ):
        if lowered.startswith(prefix):
            objective = text[len(prefix):].strip().lstrip(":").strip()
            break
    if not objective:
        return ToolResult(
            "subagent_spawn",
            content="What should the subagent do, sir?",
            success=True,
        )
    agent_def = {
        "scout": {
            "description": "Bounded read-only investigation in this repo.",
            "prompt": (
                "You are a focused investigation subagent. Read what you "
                "need, do not edit files, return a tight report under 200 "
                "words with file paths and line numbers."
            ),
        }
    }
    args = [
        claude,
        "-p",
        "--permission-mode", "plan",
        "--add-dir", str(repo),
        "--agents", json.dumps(agent_def),
    ]
    prompt = (
        f"Use the 'scout' subagent to address: {_truncate(redact(objective), 2_000)}"
    )
    result = _run_command(args, cwd=repo, input_text=prompt, timeout=300)
    if result.returncode != 0:
        return ToolResult(
            "subagent_spawn",
            content=(
                f"Subagent failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:240] or 'no stderr'}"
            ),
            success=False,
        )
    return ToolResult(
        "subagent_spawn",
        content=_truncate(result.stdout.strip() or "(empty reply)", MAX_OUTPUT_CHARS),
        success=True,
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


def perplexity_council(query: str, repo: Path | None = None) -> ToolResult:
    """Multi-model debate via `pwm council`.

    Costs 4 Pro Searches (3 models + 1 synthesis). Routed only on explicit
    council intents — see ``route_text`` for the keyword list.
    """
    repo = _resolve_repo(str(repo) if repo else None)
    pwm = shutil.which("pwm")
    if not pwm:
        return _missing("perplexity_adapter", "pwm")
    result = _run_command(
        [pwm, "council", redact(query), "--source", "web"],
        cwd=repo,
        timeout=300,
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
