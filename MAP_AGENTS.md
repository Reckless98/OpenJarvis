# OpenJarvis Agent Map

Navigation index shared by **Codex** (primary implementation) and **Claude**
(planning, review, polish). Keep this file short — it is a router, not a
spec. Update entries when paths change.

> Cross-agent continuity ledger lives at `.aria/CURRENT.md`. Run
> `aria-handoff read` (or `brief`) before starting work to pick up where
> the other agent left off.

---

## Owners by surface

| Surface | Primary owner | Notes |
| --- | --- | --- |
| `src/openjarvis/tools/` | Codex | Tools registered in `tools/__init__.py`. |
| `src/openjarvis/server/` | Codex | FastAPI routers + cockpit-only app. |
| `src/openjarvis/cli/` | Codex | CLI entrypoints (`jarvis cockpit`, etc.). |
| `frontend/src/` | Codex | React + Vite + Tailwind. |
| `tests/` | Codex (write) / Claude (review) | Pytest under `tests/`. |
| `docs/user-guide/` | Claude | Concise user-facing docs. |
| `MAP_AGENTS.md` (this file) | Both | Keep entries terse. |
| `.aria/` | Both | Append run events; never delete. |

---

## Filip Jarvis Cockpit (current focus)

The cockpit is OAuth/CLI-first. No API keys, no Ollama required.

- **Tool / router:** [`src/openjarvis/tools/filip_cockpit.py`](src/openjarvis/tools/filip_cockpit.py)
  - `route_text()`, `execute_route()`, `jarvis_chat()`, `launcher()`
  - `JARVIS_PERSONA`, `LAUNCHER_TARGETS`, `BACKEND_MODELS`
- **CLI:** [`src/openjarvis/cli/cockpit_cmd.py`](src/openjarvis/cli/cockpit_cmd.py)
- **API route:** `POST /v1/cockpit/run` → [`src/openjarvis/server/api_routes.py`](src/openjarvis/server/api_routes.py) (`cockpit_router`)
- **Cockpit-only backend:** [`src/openjarvis/server/cockpit_app.py`](src/openjarvis/server/cockpit_app.py)
- **Browser page:** [`frontend/src/pages/FilipCockpitPage.tsx`](frontend/src/pages/FilipCockpitPage.tsx)
- **Voice helpers:** [`frontend/src/lib/useVoice.ts`](frontend/src/lib/useVoice.ts) (`pickJarvisVoice` male-only cascade)
- **Wake/boot audio:** [`frontend/src/lib/wakeSounds.ts`](frontend/src/lib/wakeSounds.ts) (`bootRiff()` prefers `/boot.mp3` then synth)
- **Clap detector:** [`frontend/src/lib/useClapDetector.ts`](frontend/src/lib/useClapDetector.ts)
- **Tests:** [`tests/server/test_api_routes.py`](tests/server/test_api_routes.py) (`TestCockpitRoutes`), [`tests/tools/test_filip_cockpit.py`](tests/tools/test_filip_cockpit.py)
- **User doc:** [`docs/user-guide/filip-jarvis-cockpit.md`](docs/user-guide/filip-jarvis-cockpit.md)

User-supplied audio (gitignored, never commit):
- `frontend/public/boot.mp3` — overrides synthesized boot riff.
- `frontend/public/wake.mp3` and `wake-0..5.mp3` — override synthesized wake cues.

---

## CLI bridges the cockpit routes to

| Backend | Binary | Purpose |
| --- | --- | --- |
| `claude` | `claude -p` | Chat (Jarvis persona) + Claude reviews. |
| `codex` | `codex exec` | Implementation delegation. |
| `lumo` | `lumo-offload` | Cheap summaries/commits/docs. |
| `perplexity` | `pwm ask` | Current web search / latest docs. |
| `aria` | `aria-handoff` | Cross-agent continuity ledger. |
| `safe_shell` | direct, allowlist | Read-only repo/system status. |
| `launcher` | `xdg-open` | Open allowlisted URLs/apps. |

---

## Run / verify locally

```bash
# Backend (cockpit-only)
uv run --extra server python -m uvicorn openjarvis.server.cockpit_app:app \
  --host 127.0.0.1 --port 8000

# Frontend
( cd frontend && npm run dev )

# Lint + focused tests (cockpit surface)
uv run ruff check src/openjarvis/tools/filip_cockpit.py \
  src/openjarvis/server/api_routes.py \
  src/openjarvis/server/cockpit_app.py \
  tests/server/test_api_routes.py tests/tools/test_filip_cockpit.py
uv run pytest tests/server tests/tools/test_filip_cockpit.py \
  tests/tools/test_tool_registration.py tests/cli/test_cockpit_cmd.py -q

# Frontend build
( cd frontend && npm run build )
```

Browser: <http://localhost:5173/filip-cockpit>.

---

## Handoff protocol

1. **Start of work:** `aria-handoff brief` (or `read`) for state.
2. **During work:** keep changes scoped, stage explicit files only.
3. **End of work:** `aria-handoff append` with files, commands, verification,
   risks, next step. Optional: `aria-handoff checkpoint` for milestones.
4. **Commit:** explicit `git add file1 file2 …`, never `git add -A` or `.`.
   Do not push without the human asking.

---

## Cross-links

- Phase 3.2 baseline commit: `a9e1eb63` (Jarvis persona + launcher + boot riff).
- Phase 3.2 follow-up (this batch): humanizer for spoken results, male
  voice picker hardening, YouTube Music launcher, Spotify removed, MP3
  override for boot riff. To be committed after verification.
- See also: `.aria/CHECKPOINTS/` for prior milestone snapshots.
