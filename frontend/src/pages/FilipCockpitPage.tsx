import { useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  XCircle,
} from 'lucide-react';
import { runCockpit } from '../lib/api';
import type { CockpitRunResponse } from '../lib/api';

const QUICK_ACTIONS = [
  { label: 'Hello', command: 'hello' },
  { label: 'Repo status', command: 'show repo status' },
  { label: 'Claude review', command: 'ask Claude to review diff' },
  { label: 'Codex next step', command: 'ask Codex to inspect next step' },
  { label: 'Lumo commit', command: 'ask Lumo to draft commit message' },
  { label: 'Latest docs', command: 'search latest docs' },
  { label: 'Current state', command: 'read current state' },
];

const TOOL_LABELS: Record<string, string> = {
  codex: 'Codex',
  claude: 'Claude',
  'lumo-offload': 'Lumo',
  pwm: 'Perplexity',
  'aria-handoff': '.aria',
};

function statusColor(success?: boolean) {
  if (success === undefined) return 'var(--color-text-tertiary)';
  return success ? 'var(--color-success)' : 'var(--color-error)';
}

function missingTools(result: CockpitRunResponse | null): string[] {
  if (!result?.available) return [];
  return Object.entries(TOOL_LABELS)
    .filter(([key]) => !result.available[key])
    .map(([, label]) => label);
}

export function FilipCockpitPage() {
  const [command, setCommand] = useState('hello');
  const [repoPath, setRepoPath] = useState('~/Projects/OpenJarvis');
  const [dryRun, setDryRun] = useState(true);
  const [result, setResult] = useState<CockpitRunResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const missing = useMemo(() => missingTools(result), [result]);
  const canRun = command.trim().length > 0 && !loading;

  const submit = async () => {
    if (!canRun) return;
    setLoading(true);
    setError('');
    try {
      const next = await runCockpit({
        command,
        repo_path: repoPath,
        dry_run: dryRun,
      });
      setResult(next);
    } catch (exc) {
      setResult(null);
      setError(exc instanceof Error ? exc.message : 'Cockpit request failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8 md:py-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
        <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <div
              className="mb-2 inline-flex items-center gap-2 text-xs font-medium"
              style={{ color: 'var(--color-accent)' }}
            >
              <Sparkles size={14} />
              OAuth/CLI-first
            </div>
            <h1 className="text-xl font-semibold" style={{ color: 'var(--color-text)' }}>
              Filip Cockpit
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            <span className="inline-flex items-center gap-1.5">
              <ShieldCheck size={14} style={{ color: 'var(--color-success)' }} />
              Voice off
            </span>
            <span>API keys optional</span>
            <span>Ollama optional</span>
          </div>
        </header>

        <section
          className="grid gap-4 rounded-lg p-4 md:grid-cols-[minmax(0,1fr)_220px]"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          <label className="flex flex-col gap-2">
            <span className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
              Command
            </span>
            <textarea
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              rows={4}
              className="min-h-[112px] resize-y rounded-lg px-3 py-2 text-sm outline-none"
              style={{
                background: 'var(--color-input-bg)',
                border: '1px solid var(--color-input-border)',
                color: 'var(--color-text)',
              }}
            />
          </label>

          <div className="flex flex-col gap-3">
            <label className="flex flex-col gap-2">
              <span className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
                Repo path
              </span>
              <input
                value={repoPath}
                onChange={(event) => setRepoPath(event.target.value)}
                className="rounded-lg px-3 py-2 text-sm outline-none"
                style={{
                  background: 'var(--color-input-bg)',
                  border: '1px solid var(--color-input-border)',
                  color: 'var(--color-text)',
                }}
              />
            </label>

            <label
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-sm"
              style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text-secondary)' }}
            >
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(event) => setDryRun(event.target.checked)}
                className="h-4 w-4 accent-cyan-600"
              />
              Dry run
            </label>

            <button
              onClick={submit}
              disabled={!canRun}
              className="inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-opacity"
              style={{
                background: canRun ? 'var(--color-accent)' : 'var(--color-disabled-bg)',
                color: 'var(--color-on-accent)',
                cursor: canRun ? 'pointer' : 'not-allowed',
              }}
            >
              {loading ? <RefreshCw size={16} className="animate-spin" /> : <Play size={16} />}
              Execute
            </button>
          </div>
        </section>

        <section className="flex flex-wrap gap-2">
          {QUICK_ACTIONS.map((item) => (
            <button
              key={item.label}
              onClick={() => setCommand(item.command)}
              className="rounded-lg px-3 py-1.5 text-xs transition-colors"
              style={{
                background: command === item.command ? 'var(--color-accent-subtle)' : 'var(--color-bg-secondary)',
                border: '1px solid var(--color-border)',
                color: command === item.command ? 'var(--color-text)' : 'var(--color-text-secondary)',
              }}
            >
              {item.label}
            </button>
          ))}
        </section>

        <section className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
          <div
            className="rounded-lg p-4"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
                Route
              </h2>
              <span
                className="inline-flex items-center gap-1.5 text-xs"
                style={{ color: statusColor(result?.success) }}
              >
                {result?.success === false ? <XCircle size={14} /> : <CheckCircle2 size={14} />}
                {result ? (result.success ? 'Success' : 'Failed') : 'Idle'}
              </span>
            </div>

            <dl className="grid gap-3 text-sm">
              <div>
                <dt className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>Backend</dt>
                <dd className="mt-1 font-medium" style={{ color: 'var(--color-text)' }}>
                  {result?.backend || '—'}
                </dd>
              </div>
              <div>
                <dt className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>Action</dt>
                <dd className="mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                  {result?.action || '—'}
                </dd>
              </div>
              <div>
                <dt className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>Reason</dt>
                <dd className="mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                  {result?.reason || '—'}
                </dd>
              </div>
            </dl>

            {(missing.length > 0 || error) && (
              <div
                className="mt-4 rounded-lg p-3 text-xs"
                style={{
                  background: 'color-mix(in srgb, var(--color-warning) 8%, transparent)',
                  border: '1px solid color-mix(in srgb, var(--color-warning) 24%, transparent)',
                  color: 'var(--color-text-secondary)',
                }}
              >
                <div className="mb-1 flex items-center gap-1.5 font-medium" style={{ color: 'var(--color-warning)' }}>
                  <AlertTriangle size={14} />
                  Warning
                </div>
                {error || `Missing tools: ${missing.join(', ')}`}
              </div>
            )}
          </div>

          <div
            className="flex min-h-[360px] flex-col rounded-lg"
            style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
          >
            <div
              className="flex items-center gap-2 px-4 py-3"
              style={{ borderBottom: '1px solid var(--color-border)' }}
            >
              <SquareTerminal size={16} style={{ color: 'var(--color-accent)' }} />
              <h2 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
                Result
              </h2>
            </div>
            <pre
              className="flex-1 overflow-auto whitespace-pre-wrap break-words p-4 text-xs leading-relaxed"
              style={{ color: 'var(--color-text-secondary)' }}
            >
              {error || result?.result || 'No result yet.'}
            </pre>
          </div>
        </section>
      </div>
    </div>
  );
}
