import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, Cpu, RefreshCw, ShieldCheck, XCircle } from 'lucide-react';
import { fetchCockpitBackends, type CockpitBackendInfo } from '../lib/api';
import { loadCockpitPrefs, saveCockpitPrefs, type CockpitPrefs } from '../lib/cockpitPrefs';

type BackendsMap = Record<string, CockpitBackendInfo>;

function modelLabel(model: string): string {
  return model === '' ? 'Session default' : model;
}

export function BackendsPage() {
  const [backends, setBackends] = useState<BackendsMap | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [prefs, setPrefs] = useState<CockpitPrefs>(() => loadCockpitPrefs());

  const refresh = () => {
    setLoading(true);
    setError('');
    fetchCockpitBackends()
      .then((res) => setBackends(res.backends))
      .catch((exc) => setError(exc instanceof Error ? exc.message : 'Failed to load backends'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    refresh();
  }, []);

  const persist = (next: CockpitPrefs) => {
    setPrefs(next);
    saveCockpitPrefs(next);
  };

  const entries = useMemo(() => (backends ? Object.entries(backends) : []), [backends]);

  return (
    <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8 md:py-8">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
        <header className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <div
              className="mb-2 inline-flex items-center gap-2 text-xs font-medium"
              style={{ color: 'var(--color-accent)' }}
            >
              <ShieldCheck size={14} />
              CLI bridges only — no API keys
            </div>
            <h1 className="text-xl font-semibold" style={{ color: 'var(--color-text)' }}>
              Backends
            </h1>
            <p className="mt-1 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
              Status of local CLI bridges. Routing uses the existing CLI logins
              (Codex, Claude Code, Lumo, Perplexity, .aria). Choose a default
              backend and preferred model for explicit overrides — leave a model
              empty to use the CLI session default.
            </p>
          </div>
          <button
            onClick={refresh}
            disabled={loading}
            className="inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs font-medium"
            style={{
              background: 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-secondary)',
            }}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </header>

        {error && (
          <div
            className="rounded-lg p-3 text-sm"
            style={{
              background: 'color-mix(in srgb, var(--color-error) 8%, transparent)',
              border: '1px solid color-mix(in srgb, var(--color-error) 24%, transparent)',
              color: 'var(--color-text-secondary)',
            }}
          >
            {error}
          </div>
        )}

        <section
          className="rounded-lg p-4"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          <div className="mb-3 flex items-center gap-2">
            <Cpu size={16} style={{ color: 'var(--color-accent)' }} />
            <h2 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
              Default backend
            </h2>
          </div>
          <select
            value={prefs.defaultBackend}
            onChange={(event) =>
              persist({ ...prefs, defaultBackend: event.target.value })
            }
            className="w-full rounded-lg px-3 py-2 text-sm outline-none"
            style={{
              background: 'var(--color-input-bg)',
              border: '1px solid var(--color-input-border)',
              color: 'var(--color-text)',
            }}
          >
            <option value="">Smart routing (let Jarvis choose)</option>
            {entries.map(([key, info]) => (
              <option key={key} value={key} disabled={!info.available}>
                {info.label} {info.available ? '' : '(unavailable)'}
              </option>
            ))}
          </select>
        </section>

        <section className="grid gap-3">
          {entries.map(([key, info]) => (
            <article
              key={key}
              className="rounded-lg p-4"
              style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
            >
              <header className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {info.available ? (
                    <CheckCircle2 size={16} style={{ color: 'var(--color-success)' }} />
                  ) : (
                    <XCircle size={16} style={{ color: 'var(--color-error)' }} />
                  )}
                  <h3 className="text-sm font-semibold" style={{ color: 'var(--color-text)' }}>
                    {info.label}
                  </h3>
                </div>
                <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                  {info.available ? info.path || 'on PATH' : 'not on PATH'}
                </span>
              </header>
              <p className="mb-3 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                {info.uses}
              </p>
              {info.models.length > 1 ? (
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                    Preferred model
                  </span>
                  <select
                    value={prefs.preferredModel[key] ?? ''}
                    onChange={(event) =>
                      persist({
                        ...prefs,
                        preferredModel: {
                          ...prefs.preferredModel,
                          [key]: event.target.value,
                        },
                      })
                    }
                    disabled={!info.available}
                    className="rounded-lg px-3 py-2 text-sm outline-none"
                    style={{
                      background: 'var(--color-input-bg)',
                      border: '1px solid var(--color-input-border)',
                      color: 'var(--color-text)',
                    }}
                  >
                    {info.models.map((model) => (
                      <option key={model} value={model}>
                        {modelLabel(model)}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <p className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                  Model is fixed by this CLI; no override available.
                </p>
              )}
            </article>
          ))}
        </section>
      </div>
    </div>
  );
}
