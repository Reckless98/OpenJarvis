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
