# Filip Jarvis Cockpit — Phase 3 plan (YOLO Stark)

> **Status:** active. Phase 1 + Phase 2 shipped on `filip-jarvis-cockpit`.
> Phase 3 is the “real Stark Industries” jump: live execution, browser
> automation, real-world side effects, and conversational follow-through.

## Goal

Make the cockpit a real working butler:

1. **Live execution by default.** Codex/Claude actually run, not dry-run.
2. **Browser automation.** Playwright bridge with a persistent profile so
   Jarvis can drive logged-in sites (YouTube Music, GitHub, gmail) without
   ever storing your password — sessions persist via cookies in the profile.
3. **Real-world side effects.** “make me a project X” creates the directory
   and git-inits it. “play me <song>” lands on the exact YouTube track.
4. **Conversational pause.** Jarvis can stop mid-route and ask “how would
   you like to proceed?”, listen for your answer, and continue.
5. **Auto-mode browser.** Headless when the persistent profile is already
   logged in for the target domain; headed (visible) when it isn't.

## What changed in the global policy

Filip explicitly asked for YOLO mode. `~/.claude/CLAUDE.md` Safety section
was relaxed (this turn). New rules:

- Persistent browser profiles are allowed at
  `~/.openjarvis/playwright-profile/`. Sessions persist via cookies only.
- OS keyring (`secret-tool`, libsecret, gnome-keyring) lookups are allowed.
- **Still hard-blocked:** writing plaintext passwords to repo files,
  reading `~/.ssh/`, `~/.gnupg/`, `~/.aws/credentials`, `~/.config/gh/hosts.yml`,
  committing secrets, `rm -rf /` style destructive shell, production-DB
  mutation without an explicit per-call confirmation.

## Component map

| # | Component | File(s) | Status |
|---|-----------|---------|--------|
| 1 | Plan doc | `docs/user-guide/filip-jarvis-cockpit-phase3.md` | this doc |
| 2 | Policy relax | `~/.claude/CLAUDE.md` | done this turn |
| 3 | Dry-run default OFF | `frontend/src/pages/FilipCockpitPage.tsx` | done this turn |
| 4 | `make_project` tool | `src/openjarvis/tools/filip_cockpit.py` | done this turn |
| 5 | Exact-song play | `src/openjarvis/tools/filip_cockpit.py` (launcher) | done this turn |
| 6 | Playwright bridge — skeleton | `src/openjarvis/tools/playwright_bridge.py` | done this turn (navigate / click / screenshot / fill / wait) |
| 7 | Playwright bridge — auto-mode | same | done this turn (cookie-presence probe) |
| 8 | Clarify/pause flow | `FilipCockpitPage.tsx` + route action `"clarify"` | done this turn |
| 9 | Routing: browser intents → playwright_bridge | `route_text()` | done this turn |
| 10 | Routing: project intents → make_project | `route_text()` | done this turn |
| 11 | Tests | `tests/tools/test_playwright_bridge.py`, `tests/tools/test_make_project.py` | done this turn |

## Auto-mode logic (headed vs headless)

When the cockpit gets a browser request:

1. Look up the target domain (e.g. `music.youtube.com`).
2. Check `~/.openjarvis/playwright-profile/Default/Cookies` for any cookie
   on that domain whose `expires_utc` is in the future.
3. If a non-expired session cookie exists → headless.
4. Otherwise → headed, with a status line: “Headed mode — log in to
   <domain> once and Jarvis will go headless next time.”

This means **first time on a new site = visible window, manual login,
done. Every call after = silent headless.** No password storage.

## What Jarvis can now do end-to-end

- **“play me Don’t Tread on Me by Cain on YouTube”** → routes to launcher
  → opens YouTube Music search with the exact query → YT autoplays the
  top result. (Playwright bridge can be flipped in later if you want
  guaranteed-exact play via DOM click.)
- **“make me a project called todo-app”** → creates
  `~/Projects/todo-app/` with `git init`, `README.md`, `.gitignore`.
- **“ask Codex to implement the rest”** → real `codex exec` run, output
  streamed back.
- **“open YouTube and search for my watch later”** (with persistent
  profile) → Playwright headless, returns page title + first 5 items.
- **“pause and ask me how to proceed”** → route returns
  `action="clarify"`, frontend speaks the question, listens for
  follow-up, re-submits the original + clarification.

## Deferred to Phase 3.1 (next turn)

- Streaming Codex/Claude output (currently still buffered; the call
  blocks for the full turn).
- Full duplex audio (Jarvis interrupts you mid-sentence). Current
  conversational pattern is half-duplex: he asks, you answer.
- Browser-driven posting (tweets, comments, form submits) requires its
  own per-call confirmation gate even in YOLO.
- Keyring-backed credential helper (`secret-tool lookup`) — keyring is
  *allowed* now but no helper UI yet.

## Risks (kept honest)

- Persistent browser profile is on-disk plaintext: if your machine is
  compromised, the attacker gets your logged-in sessions. Same risk
  profile as your normal Chrome.
- Real Codex/Claude runs eat tokens. Default dry-run flip is OFF; the
  checkbox is still there.
- `make_project` writes to `~/Projects/`. Name is sanitized but if you
  pass `../../../etc/foo` the sanitizer rejects it.
- Playwright bridge can navigate to any URL. There's no allowlist beyond
  the launcher's. If you ask Jarvis to “open evil.com”, he will.
