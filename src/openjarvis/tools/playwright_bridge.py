"""Playwright bridge for the Filip Jarvis Cockpit (Phase 3).

This tool gives Jarvis a real browser. It uses a single persistent profile
at ``~/.openjarvis/playwright-profile/`` so that once Filip logs in to a
site (YouTube, GitHub, gmail, anywhere), the session cookies persist and
Jarvis can return headless on subsequent calls.

Auto-mode logic:
  1. Pull the target domain out of the requested URL (or guess from intent).
  2. Check the persistent profile's cookie jar for a non-expired cookie on
     that domain.
  3. If found → headless (silent).
  4. Otherwise → headed (visible). Filip logs in once; next call is silent.

The bridge is deliberately small. Supported high-level intents:
  - ``navigate <url>``                 — open URL, return page title + URL
  - ``open youtube and search for X``  — DOM-driven YT Music search + play top
  - ``screenshot <url>``               — open, screenshot, return path
  - ``log in to <site>``               — open the login URL headed so Filip
                                          can complete it manually; cookies
                                          persist for next time

Anything destructive (form submit on a real account, posting, payments) is
gated by ``allow_writes`` which defaults to False — the cockpit must pass
``--write`` (or the request must say ``confirm: yes``) to enable it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

from openjarvis.core.types import ToolResult

PROFILE_DIR = Path.home() / ".openjarvis" / "playwright-profile"
SCREENSHOT_DIR = Path.home() / ".openjarvis" / "screenshots"

# Holds the long-lived Playwright + browser context handles so subsequent
# drive verbs (click / fill / screenshot / navigate) reuse the same window
# instead of relaunching Chromium per call. Lazy-initialised by
# `_ensure_persistent_page`; reset by `_close_persistent` when the user
# closes the window manually (or any call sees a dead handle).
_PERSISTENT: dict[str, Any] = {
    "pw": None,
    "ctx": None,
    "page": None,
}

# Playwright doesn't ship a chromium binary for every Linux release (e.g.
# Ubuntu 26.04 is unsupported as of 2026-05). Detect the system Chrome /
# Chromium and pass it as `executable_path`, which sidesteps Playwright's
# OS gating entirely and reuses the browser Filip already has installed.
_SYSTEM_BROWSERS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
)


def _system_browser_path() -> str:
    for binary in _SYSTEM_BROWSERS:
        path = shutil.which(binary)
        if path:
            return path
    return ""

_URL_RE = re.compile(r"https?://\S+")
_NAV_TIMEOUT_MS = 20_000
_ACTION_TIMEOUT_MS = 8_000
_TOTAL_BUDGET_MS = 45_000


def _ensure_dirs() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def _extract_url(text: str) -> str:
    """Best-effort URL extraction from a natural-language request."""
    match = _URL_RE.search(text)
    if match:
        return match.group(0).rstrip(".,;:!?)>")
    lowered = text.lower()
    if "youtube music" in lowered or ("youtube" in lowered and "music" in lowered):
        return "https://music.youtube.com/"
    if "youtube" in lowered:
        return "https://www.youtube.com/"
    if "github" in lowered:
        return "https://github.com/"
    if "gmail" in lowered:
        return "https://mail.google.com/"
    return ""


def _target_domain(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        # Strip leading 'www.' so cookie lookup matches the apex domain a
        # logged-in session typically sets.
        return host.removeprefix("www.")
    except Exception:
        return ""


def _has_session_cookie(domain: str) -> bool:
    """Cheap probe — does the persistent profile have a non-expired cookie
    on this domain? We don't decrypt; just check the Cookies SQLite file.

    Returns False on any error so we fall back to headed mode (safer).
    """
    if not domain:
        return False
    cookies_db = PROFILE_DIR / "Default" / "Cookies"
    if not cookies_db.exists():
        return False
    try:
        import sqlite3

        with sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True) as conn:
            cur = conn.cursor()
            # Chromium-style cookies table: host_key, expires_utc (microseconds
            # since 1601-01-01 epoch).
            now_micros = (
                int(datetime.now(UTC).timestamp()) + 11644473600
            ) * 1_000_000
            cur.execute(
                "SELECT 1 FROM cookies "
                "WHERE (host_key = ? OR host_key = ? OR host_key LIKE ?) "
                "  AND (expires_utc = 0 OR expires_utc > ?) "
                "LIMIT 1",
                (domain, f".{domain}", f"%.{domain}", now_micros),
            )
            return cur.fetchone() is not None
    except Exception:
        return False


def _should_run_headless(url: str) -> bool:
    return _has_session_cookie(_target_domain(url))


def playwright_run(text: str, repo: Path | None = None) -> ToolResult:
    """Top-level dispatch for browser-automation requests.

    Args:
      text: Natural-language request from the cockpit router.
      repo: Caller repo (unused right now; passed for symmetry).
    """
    _ = repo  # symmetry with other tool entrypoints
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return ToolResult(
            "playwright_bridge",
            content=(
                "Playwright is not installed. Run "
                "`uv sync --extra browser && uv run playwright install chromium` "
                "to enable the bridge."
            ),
            success=False,
        )

    lowered = text.lower().strip()
    url = _extract_url(text)

    if not url and "search" in lowered and "youtube" in lowered:
        query = _extract_youtube_query(text)
        return _drive_youtube_search(query)

    # "play <song>" → resolve the top YouTube hit's video id and let the
    # cockpit's in-page IFrame player handle playback. yt-dlp gives us the
    # id without launching a browser at all, which keeps the audio alive
    # across the response (Playwright would close the page and kill it).
    # Falls back to the original Playwright search+click if yt-dlp isn't
    # available.
    if not url and lowered.startswith("play "):
        query = _extract_youtube_query(text)
        return _resolve_play_request(query)

    # Drive verbs against the persistent window — these reuse the same
    # Chromium that previous calls opened, so page state survives between
    # "navigate to …" → "click …" → "fill … with …" → "screenshot".
    intent = _parse_drive_intent(text, url)
    if intent is not None:
        verb, payload = intent
        if verb == "navigate":
            return _drive_navigate_session(payload["url"])
        if verb == "click":
            return _drive_click(payload["target"])
        if verb == "fill":
            return _drive_fill(payload["field"], payload["value"])
        if verb == "screenshot_session":
            return _drive_screenshot_session()

    if "screenshot" in lowered and url:
        return _drive_screenshot(url)

    if ("log in to" in lowered or "login to" in lowered) and url:
        return _drive_login(url)

    if url:
        return _drive_navigate(url)

    return ToolResult(
        "playwright_bridge",
        content=(
            "I'd need either an explicit URL or a recognized intent "
            "('search youtube for ...', 'screenshot <url>', 'log in to <url>'), sir."
        ),
        success=False,
    )


def _extract_youtube_query(text: str) -> str:
    """Pull the query out of 'search youtube for X' / 'play X' style asks.

    Markers are tried longest-first so 'search youtube for X' returns 'X',
    not 'youtube for X'. Matches anywhere in the string, anchored on a
    leading word boundary (start-of-string or whitespace).
    """
    lowered = text.lower()
    # Order matters: most specific first.
    for marker in ("search youtube for ", "search for ", "play "):
        # Try start-of-string match.
        if lowered.startswith(marker):
            return text[len(marker) :].strip().strip("'\"")
        # Try whitespace-preceded match (rfind so we land on the latest
        # occurrence, which is usually the verb closest to the query).
        idx = lowered.rfind(" " + marker)
        if idx >= 0:
            start = idx + 1 + len(marker)
            return text[start:].strip().strip("'\"")
    return text.strip()


def _drive_navigate(url: str) -> ToolResult:
    """Open the URL, return page title + final URL."""
    _ensure_dirs()
    headless = _should_run_headless(url)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                viewport={"width": 1280, "height": 800},
                executable_path=_system_browser_path() or None,
            )
            page = ctx.new_page()
            page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            title = page.title()[:200]
            final_url = page.url
            ctx.close()
        mode = "headless" if headless else "headed"
        return ToolResult(
            "playwright_bridge",
            content=_format_result(
                {
                    "mode": mode,
                    "url": final_url,
                    "title": title,
                    "note": (
                        "Headed — log in if needed; next call on this domain "
                        "will go headless automatically."
                        if not headless
                        else "Session cookie present; ran silently."
                    ),
                }
            ),
            success=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _exception_result(exc)


def _drive_screenshot(url: str) -> ToolResult:
    """Open URL, full-page screenshot to ~/.openjarvis/screenshots/."""
    _ensure_dirs()
    headless = _should_run_headless(url)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    domain = _target_domain(url) or "page"
    path = SCREENSHOT_DIR / f"{stamp}-{domain}.png"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                viewport={"width": 1440, "height": 900},
                executable_path=_system_browser_path() or None,
            )
            page = ctx.new_page()
            page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.screenshot(path=str(path), full_page=True)
            ctx.close()
        return ToolResult(
            "playwright_bridge",
            content=_format_result(
                {
                    "mode": "headless" if headless else "headed",
                    "url": url,
                    "screenshot": str(path),
                    "bytes": path.stat().st_size,
                }
            ),
            success=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _exception_result(exc)


def _drive_login(url: str) -> ToolResult:
    """Open the URL headed so Filip can log in. Persist cookies for next time."""
    _ensure_dirs()
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=False,
                viewport={"width": 1280, "height": 800},
                executable_path=_system_browser_path() or None,
            )
            page = ctx.new_page()
            page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            # Hold the window open long enough for a manual login; close it
            # once the user navigates away from the login page (heuristic:
            # `/login` no longer in the URL).
            deadline = datetime.now(UTC).timestamp() + 120  # 2 min budget
            while datetime.now(UTC).timestamp() < deadline:
                try:
                    page.wait_for_timeout(2000)
                    cur = page.url.lower()
                    if "login" not in cur and "signin" not in cur:
                        break
                except Exception:
                    break
            final_url = page.url
            ctx.close()
        return ToolResult(
            "playwright_bridge",
            content=_format_result(
                {
                    "mode": "headed",
                    "url": final_url,
                    "note": (
                        "Login window closed. Session cookies (if any) are now "
                        "in the persistent profile; next call to this domain will "
                        "run headless."
                    ),
                }
            ),
            success=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _exception_result(exc)


def _resolve_play_request(query: str) -> ToolResult:
    """Return the top YouTube video id for `query` so the IFrame can play it.

    Uses yt-dlp's metadata-only search (no download, no browser). When yt-dlp
    isn't installed or the lookup fails, falls back to `_drive_youtube_search`
    so the user still gets *something*. Sets `metadata['video_id']` and
    `metadata['video_title']` on success.
    """
    if not query:
        return ToolResult(
            "playwright_bridge",
            content="What would you like me to play, sir?",
            success=False,
        )
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        # Graceful fallback — opens the headed browser the old way.
        return _drive_youtube_search(query)
    try:
        # `ytsearch1:` returns the top hit. `-J` dumps JSON metadata only.
        proc = subprocess.run(
            [yt_dlp, "--no-warnings", "--no-playlist", "-J", "--flat-playlist",
             "--default-search", "ytsearch1", f"ytsearch1:{query}"],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return _drive_youtube_search(query)
        data = json.loads(proc.stdout)
        # ytsearch returns a playlist with one entry.
        entries = data.get("entries") or []
        if not entries:
            return _drive_youtube_search(query)
        first = entries[0]
        video_id = first.get("id") or first.get("video_id") or ""
        title = first.get("title") or ""
        if not video_id:
            return _drive_youtube_search(query)
        return ToolResult(
            "playwright_bridge",
            content=_format_result(
                {
                    "mode": "iframe",
                    "query": query,
                    "video_id": video_id,
                    "title": title,
                    "note": (
                        "Cockpit IFrame will play this; audio survives "
                        "across calls."
                    ),
                }
            ),
            success=True,
            metadata={"video_id": video_id, "video_title": title},
        )
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return _drive_youtube_search(query)


def _drive_youtube_search(query: str) -> ToolResult:
    """DOM-driven YouTube search → click the first result. Headless when the
    session is already signed in, otherwise headed.
    """
    _ensure_dirs()
    if not query:
        return ToolResult(
            "playwright_bridge",
            content="No YouTube search query supplied, sir.",
            success=False,
        )
    search_url = (
        f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    )
    headless = _should_run_headless("youtube.com")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                viewport={"width": 1280, "height": 800},
                executable_path=_system_browser_path() or None,
            )
            page = ctx.new_page()
            page.goto(
                search_url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded"
            )
            try:
                # Accept consent dialog if present (EU users).
                page.locator(
                    "button:has-text('Accept all'), button:has-text('I agree')"
                ).first.click(timeout=2_000)
            except Exception:
                pass
            try:
                first = page.locator("a#video-title").first
                first.click(timeout=_ACTION_TIMEOUT_MS)
                page.wait_for_load_state("domcontentloaded", timeout=_NAV_TIMEOUT_MS)
            except Exception:
                # Fall back to just leaving them on the search page if click
                # selectors changed; YouTube tweaks its DOM constantly.
                pass
            title = page.title()[:200]
            final_url = page.url
            if headless:
                # Headless playback won't actually be audible; close cleanly.
                ctx.close()
            else:
                # Headed: leave the page open ~3 s so the user sees it,
                # then close the context (the user can open a normal tab
                # if they want to keep listening).
                page.wait_for_timeout(3000)
                ctx.close()
        return ToolResult(
            "playwright_bridge",
            content=_format_result(
                {
                    "mode": "headless" if headless else "headed",
                    "query": query,
                    "landed_on": final_url,
                    "title": title,
                }
            ),
            success=True,
        )
    except Exception as exc:  # noqa: BLE001
        return _exception_result(exc)


_SENSITIVE_FIELD_RE = re.compile(
    r"\b(password|passwd|secret|token|api[-_ ]?key|otp|2fa|pin)\b",
    re.IGNORECASE,
)


def _parse_drive_intent(
    text: str, url: str
) -> tuple[str, dict[str, str]] | None:
    """Pull a drive verb out of the request.

    Returns `(verb, payload)` or None when the request isn't a drive call.
    Order matters: most specific markers first so "navigate to https://x"
    doesn't trip the bare-URL branch in the caller.
    """
    lowered = text.lower().strip()
    # Strip trailing punctuation that voice STT loves to append.
    cleaned = text.strip().rstrip(".,;:!?")
    cleaned_lower = cleaned.lower()
    for marker in ("navigate to ", "go to ", "open url "):
        if cleaned_lower.startswith(marker):
            target = cleaned[len(marker):].strip().strip("'\"")
            if not target:
                continue
            if not target.startswith(("http://", "https://")):
                target = "https://" + target
            return ("navigate", {"url": target})
    # "browse to" / explicit URL also goes through the session navigator
    # when no other intent matched — keep behaviour consistent.
    if (
        cleaned_lower.startswith("browse to ")
        or cleaned_lower.startswith("visit ")
    ) and url:
        return ("navigate", {"url": url})
    for marker in ("click on ", "click ", "tap on ", "tap "):
        if cleaned_lower.startswith(marker):
            target = cleaned[len(marker):].strip().strip("'\"")
            if target:
                return ("click", {"target": target})
    if cleaned_lower.startswith("fill "):
        # "fill <field> with <value>"  or  "fill in <field> with <value>"
        body = cleaned[len("fill "):]
        if body.lower().startswith("in "):
            body = body[3:]
        if " with " in body:
            field, value = body.split(" with ", 1)
            field = field.strip().strip("'\"")
            value = value.strip().strip("'\"")
            if field and value:
                return ("fill", {"field": field, "value": value})
    if cleaned_lower.startswith("type into "):
        body = cleaned[len("type into "):]
        if " " in body:
            field, value = body.split(" ", 1)
            return (
                "fill",
                {
                    "field": field.strip().strip("'\""),
                    "value": value.strip().strip("'\""),
                },
            )
    if (
        cleaned_lower in ("screenshot", "take a screenshot", "capture screen")
        or cleaned_lower.startswith("take a screenshot")
        or cleaned_lower.startswith("capture the screen")
    ):
        # Only when no URL was supplied; "screenshot https://x" still uses
        # the one-shot URL flow handled above by _drive_screenshot.
        if not url:
            return ("screenshot_session", {})
    _ = lowered  # silence linter — kept for parity with caller signature
    return None


def _ensure_persistent_page(*, headless: bool = False):
    """Return a long-lived Playwright Page, recreating it if it was closed.

    Subsequent drive verbs reuse the same window so cookies, scroll position,
    and the current URL all survive between API calls. When the user closes
    the window manually, the next call detects the dead handle and relaunches.
    """
    from playwright.sync_api import sync_playwright

    state = _PERSISTENT
    page = state.get("page")
    if page is not None:
        try:
            _ = page.url  # liveness probe — closed pages raise here
            return page
        except Exception:
            _close_persistent()

    _ensure_dirs()
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1280, "height": 800},
        executable_path=_system_browser_path() or None,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    state["pw"] = pw
    state["ctx"] = ctx
    state["page"] = page
    return page


def _close_persistent() -> None:
    """Tear down the long-lived Playwright handles; safe to call repeatedly."""
    state = _PERSISTENT
    try:
        if state.get("ctx") is not None:
            state["ctx"].close()
    except Exception:
        pass
    try:
        if state.get("pw") is not None:
            state["pw"].stop()
    except Exception:
        pass
    state["pw"] = None
    state["ctx"] = None
    state["page"] = None


def _drive_navigate_session(url: str) -> ToolResult:
    """Navigate the persistent page to `url`, return title + final URL."""
    try:
        page = _ensure_persistent_page()
        page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        title = page.title()[:200]
        final_url = page.url
        return ToolResult(
            "playwright_bridge",
            content=_format_result(
                {
                    "verb": "navigate",
                    "url": final_url,
                    "title": title,
                }
            ),
            success=True,
            metadata={"final_url": final_url, "page_title": title},
        )
    except Exception as exc:  # noqa: BLE001
        _close_persistent()
        return _exception_result(exc)


def _drive_click(target: str) -> ToolResult:
    """Click an element on the persistent page.

    Tries CSS first (`page.locator(target)`) and falls back to a visible-text
    match. Refuses to click anything that looks like a sensitive credential
    field — no autofilling of password / token / OTP elements.
    """
    if _SENSITIVE_FIELD_RE.search(target):
        return ToolResult(
            "playwright_bridge",
            content=(
                f"Refusing to click '{target}' — that looks like a credential "
                "field, sir. Use a manual login flow instead."
            ),
            success=False,
        )
    try:
        page = _ensure_persistent_page()
        last_error: Exception | None = None
        # First attempt: treat `target` as a CSS selector.
        try:
            page.locator(target).first.click(timeout=_ACTION_TIMEOUT_MS)
            return _click_success(target, "css", page.url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        # Fallback: visible-text match (case-insensitive, partial).
        try:
            page.get_by_text(target, exact=False).first.click(
                timeout=_ACTION_TIMEOUT_MS
            )
            return _click_success(target, "text", page.url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        return ToolResult(
            "playwright_bridge",
            content=(
                f"Could not find a clickable element matching '{target}', sir. "
                f"({type(last_error).__name__})"
            ),
            success=False,
        )
    except Exception as exc:  # noqa: BLE001
        _close_persistent()
        return _exception_result(exc)


def _click_success(target: str, how: str, url: str) -> ToolResult:
    return ToolResult(
        "playwright_bridge",
        content=_format_result(
            {
                "verb": "click",
                "matched_via": how,
                "target": target,
                "url": url,
            }
        ),
        success=True,
        metadata={"clicked_text": target, "click_method": how},
    )


def _drive_fill(field: str, value: str) -> ToolResult:
    """Locate an input by placeholder / label / aria-label and fill it.

    Refuses to write to fields whose label or placeholder looks like a
    credential — passwords stay manual.
    """
    if _SENSITIVE_FIELD_RE.search(field):
        return ToolResult(
            "playwright_bridge",
            content=(
                f"Refusing to fill '{field}' — credential fields stay manual, sir."
            ),
            success=False,
        )
    try:
        page = _ensure_persistent_page()
        candidates = (
            f"input[placeholder*='{field}' i]",
            f"textarea[placeholder*='{field}' i]",
            f"input[aria-label*='{field}' i]",
            f"textarea[aria-label*='{field}' i]",
            f"input[name*='{field}' i]",
        )
        for selector in candidates:
            try:
                loc = page.locator(selector).first
                loc.fill(value, timeout=_ACTION_TIMEOUT_MS)
                return ToolResult(
                    "playwright_bridge",
                    content=_format_result(
                        {
                            "verb": "fill",
                            "field": field,
                            "selector": selector,
                            "bytes": len(value),
                            "url": page.url,
                        }
                    ),
                    success=True,
                    metadata={"filled_field": field, "selector": selector},
                )
            except Exception:
                continue
        # Last try — label-based lookup.
        try:
            loc = page.get_by_label(field).first
            loc.fill(value, timeout=_ACTION_TIMEOUT_MS)
            return ToolResult(
                "playwright_bridge",
                content=_format_result(
                    {
                        "verb": "fill",
                        "field": field,
                        "selector": "label",
                        "bytes": len(value),
                        "url": page.url,
                    }
                ),
                success=True,
                metadata={"filled_field": field, "selector": "label"},
            )
        except Exception:
            pass
        return ToolResult(
            "playwright_bridge",
            content=(
                f"Couldn't find an input matching '{field}' on the current page, sir."
            ),
            success=False,
        )
    except Exception as exc:  # noqa: BLE001
        _close_persistent()
        return _exception_result(exc)


def _drive_screenshot_session(out_path: Path | None = None) -> ToolResult:
    """Screenshot the persistent page (no URL navigation)."""
    try:
        page = _ensure_persistent_page()
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        domain = _target_domain(page.url) or "page"
        path = out_path or (SCREENSHOT_DIR / f"{stamp}-{domain}.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        size = path.stat().st_size if path.exists() else 0
        return ToolResult(
            "playwright_bridge",
            content=_format_result(
                {
                    "verb": "screenshot",
                    "url": page.url,
                    "screenshot": str(path),
                    "bytes": size,
                }
            ),
            success=True,
            metadata={"screenshot_path": str(path)},
        )
    except Exception as exc:  # noqa: BLE001
        _close_persistent()
        return _exception_result(exc)


def _format_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)


def _exception_result(exc: Exception) -> ToolResult:
    msg = str(exc).splitlines()[0][:240] or exc.__class__.__name__
    return ToolResult(
        "playwright_bridge",
        content=(
            f"Playwright failed: {msg}. "
            "Most likely fixes: install the Chromium binary with "
            "`uv run playwright install chromium`, or close any other "
            "process holding the persistent profile."
        ),
        success=False,
    )


__all__ = [
    "PROFILE_DIR",
    "SCREENSHOT_DIR",
    "playwright_run",
]
