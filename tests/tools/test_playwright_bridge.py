"""Tests for the Playwright bridge (Phase 3).

These tests intentionally do NOT spin up a real browser — they verify the
URL/intent parser, the cookie probe, and the missing-dependency error path.
The actual end-to-end browser drive is exercised by `make smoke-playwright`
(out-of-band) so CI doesn't need a Chromium download.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openjarvis.tools import playwright_bridge as pb


def test_extract_url_finds_explicit_url() -> None:
    assert pb._extract_url("please open https://example.com today") == (
        "https://example.com"
    )


def test_extract_url_falls_back_to_known_sites() -> None:
    assert "music.youtube.com" in pb._extract_url("open youtube music for me")
    assert "youtube.com" in pb._extract_url("youtube search")
    assert "github.com" in pb._extract_url("open github please")
    assert "mail.google.com" in pb._extract_url("check my gmail")


def test_extract_url_returns_empty_when_nothing_matches() -> None:
    assert pb._extract_url("hello sir how are you") == ""


def test_target_domain_strips_www_prefix() -> None:
    assert pb._target_domain("https://www.youtube.com/results?x=1") == "youtube.com"
    assert pb._target_domain("https://music.youtube.com/") == "music.youtube.com"
    assert pb._target_domain("not-a-url") == ""


def test_has_session_cookie_false_when_db_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pb, "PROFILE_DIR", tmp_path / "no-profile")
    assert pb._has_session_cookie("youtube.com") is False


def test_has_session_cookie_true_when_cookie_present(
    tmp_path: Path, monkeypatch
) -> None:
    """Write a real Chromium-shape Cookies SQLite file with a fresh cookie."""
    profile = tmp_path / "profile"
    default = profile / "Default"
    default.mkdir(parents=True)
    cookies_db = default / "Cookies"
    # expires_utc is microseconds since 1601-01-01. Pick "now + 30 days".
    future_micros = int(
        (datetime.now(UTC) + timedelta(days=30)).timestamp() + 11644473600
    ) * 1_000_000
    with sqlite3.connect(cookies_db) as conn:
        conn.execute(
            "CREATE TABLE cookies ("
            "  host_key TEXT, name TEXT, value TEXT, expires_utc INTEGER"
            ")"
        )
        conn.execute(
            "INSERT INTO cookies VALUES (?, ?, ?, ?)",
            (".youtube.com", "SID", "abc", future_micros),
        )
        conn.commit()
    monkeypatch.setattr(pb, "PROFILE_DIR", profile)
    assert pb._has_session_cookie("youtube.com") is True


def test_playwright_run_reports_missing_dependency(monkeypatch) -> None:
    """If `playwright` import fails, the tool returns a clear install hint."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("simulated missing playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = pb.playwright_run("open https://example.com")
    assert result.success is False
    assert "playwright install chromium" in result.content
    assert "uv sync --extra browser" in result.content


def test_extract_youtube_query_pulls_after_search_or_play() -> None:
    assert (
        pb._extract_youtube_query("search youtube for don't tread on me")
        == "don't tread on me"
    )
    assert (
        pb._extract_youtube_query("play me dont tread on me")
        == "me dont tread on me"
    )
