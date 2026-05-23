# Filip Jarvis Cockpit

The Filip Jarvis cockpit is a text-first local router for an existing AI/dev
stack. It keeps OpenJarvis as the shell and delegates work to local tools that
already have their own authentication.

The cockpit is OAuth/CLI-first. It does not require `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY`: Codex uses `codex exec` through the existing Codex login,
Claude uses `claude -p` through the existing Claude Code Pro login, Lumo uses
`lumo-offload`, Perplexity uses `pwm ask`, and continuity uses `aria-handoff`.
Missing API keys do not fail cockpit startup. Missing Ollama/local engines do
not fail cockpit mode; they only affect normal OpenJarvis model chat.

## Commands

```bash
uv run jarvis cockpit "show system status"
uv run jarvis cockpit --dry-run "ask Claude to review this diff"
uv run jarvis cockpit "search latest docs for this library"
```

## Browser UI

Start the cockpit-only backend and Vite frontend:

```bash
uv run --extra server python -m uvicorn openjarvis.server.cockpit_app:app --host 127.0.0.1 --port 8000
cd frontend
npm run dev
```

Open `http://localhost:5173/filip-cockpit`. The page uses the same cockpit
router as the CLI through `POST /v1/cockpit/run`; it does not require OpenAI or
Anthropic API keys, and it does not require Ollama for cockpit requests.

If you already have a normal OpenJarvis engine running, `uv run jarvis serve
--port 8000` also exposes the same cockpit API alongside chat routes.

Use dry-run first for route checks, then disable dry-run for bounded local
smokes such as `show repo status`.

The route is deterministic:

| Request type | Backend |
| --- | --- |
| implement, fix, refactor, test | Codex CLI |
| review, architecture, edge cases | Claude Code |
| summarize, docs, changelog, commit message | Lumo |
| latest docs, current APIs, web search | Perplexity via `pwm` |
| handoff, checkpoint, continue | `.aria` |
| system or repo status | safe shell allowlist |

Routing uses first-match precedence in this order: `.aria` continuity,
Perplexity/current web, Claude review, Lumo summary/drafting, Codex coding, then
safe shell status. Use `--dry-run --json` before execution when a request is
ambiguous.

## Tools

Enable the cockpit tools in config:

```toml
[tools]
enabled = [
  "filip_route",
  "codex_adapter",
  "claude_adapter",
  "lumo_adapter",
  "perplexity_adapter",
  "aria_handoff",
  "safe_shell",
]
```

`configs/openjarvis/examples/filip-jarvis.toml` includes a complete example.

## Safety

The cockpit does not read `.env` files, private keys, cloud credential folders,
or secret-looking paths. It does not hardcode API keys or require API-key
configuration. API-key-based engine config, if added later, should stay
optional and separate from cockpit routing.

The shell adapter only allows small read-only status commands such as
`git status`, `git diff --stat`, `df -h`, `free -h`, `nvidia-smi`, `uptime`,
`whoami`, `uname -a`, and `ss -tulpn`.

## Handoff

When a repository has `.aria/`, route decisions append compact JSONL events to
`.aria/RUN_LEDGER.jsonl`. If the current repo does not have `.aria/`, the
adapter falls back to `/home/zer0/Projects/filip-dev-arsenal/.aria` when it is
present. Repositories without an initialized run ledger do not get a new ledger
created implicitly.

## Voice Later

Voice is intentionally not part of the MVP. Text routing should work first:

```text
text command -> router -> Codex/Claude/Lumo/Perplexity/.aria/shell -> response/log
```

After that, add STT/TTS and a wake layer that feeds the same router.

## Cockpit-only mode (no API keys, no Ollama)

`cockpit_app.py` is a cockpit-only backend with no inference engine. When the
frontend detects `engine === "cockpit"` via `GET /v1/info`, it switches into
cockpit-only UX:

- Sidebar hides Chat / Dashboard / Agents / Data Sources / Settings /
  Get Started.
- `/` lands on `/filip-cockpit` directly.
- `SetupScreen` (Ollama + model + server) is bypassed.
- A `/backends` page replaces Settings/Models. It shows CLI bridge status
  (Codex, Claude Code, Lumo, Perplexity, .aria, safe shell) and lets you pick
  a preferred Anthropic / OpenAI model per backend — **without any API-key
  input**. Models are passed to `claude -p --model X` / `codex exec --model X`
  using the existing CLI logins.

The full surface (with Chat/Agents/Settings) is still available by running
`uv run jarvis serve` instead of `cockpit_app:app`; both mount the same
`/v1/cockpit/run` and `/v1/cockpit/backends` routes.

## Wake + voice (Tony Stark mode)

On `/filip-cockpit`:

- **Wake mode** toggle: Off / Clap-clap / Always-on.
- **Clap detector** uses `AudioContext` + `AnalyserNode` entirely in the
  browser; no audio leaves the device. Two RMS spikes within 1.5s fire the
  wake action.
- On wake, the page plays `/wake.mp3` if you drop one in `frontend/public/`;
  otherwise it synthesizes a short three-note chord with `OscillatorNode` so
  the experience works out of the box.
- **STT**: `SpeechRecognition` (Chrome/Edge full, Safari recent; Firefox falls
  back to push-to-talk via the mic button).
- **TTS**: `speechSynthesis` reads replies aloud when the TTS toggle is on.
- Voice/wake are **off by default**, require a user gesture to start the
  AudioContext, show a visible "listening" indicator with a mic-level meter,
  and never send raw audio off-device.

After STT resolves, the transcript is posted to `POST /v1/cockpit/run`
exactly like a typed command — same routing, same Codex/Claude/Lumo/
Perplexity/.aria/safe-shell backends.

## Status (Phase 3 baseline)

- **Plan:** Phase 1 (CLI router + `filip_cockpit` tool) and Phase 2 (browser
  surface at `/filip-cockpit` over the same router) are shipped. Phase 3
  (voice + clap wake) is deferred.
- **Risks:** `cockpit_app.py` exposes intentionally empty bootstrap endpoints
  (`/v1/models`, `/v1/info`, `/v1/savings`, `/v1/managed-agents`,
  `/v1/approvals/pending`) so the existing frontend can load without a chat
  engine — for the full surface use `uv run jarvis serve`. `npm audit`
  findings and Vite chunk-size warnings remain unaddressed. `.aria/` is
  per-developer (gitignored).
- **Tests:** `uv run ruff check` (cockpit surface), `uv run pytest` (48
  passed across cockpit/CLI/tool test files), `python3 -m py_compile`,
  `(cd frontend && npm run build)`.
- **Known issues:** `frontend/node_modules/` must be installed
  (`cd frontend && npm install`) before the build runs cleanly.
- **Next step:** Phase 3 — voice/clap wake on `/filip-cockpit` (browser
  `SpeechRecognition` + `AudioContext` RMS, toggle off by default, no API
  keys, no server-side audio).
