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

## Status (Phase 2 baseline)

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
