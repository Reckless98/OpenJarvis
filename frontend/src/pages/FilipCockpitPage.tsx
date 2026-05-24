import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Ear,
  Mic,
  MicOff,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Volume2,
  VolumeX,
  XCircle,
} from 'lucide-react';
import { JarvisCircle, type JarvisState } from '../components/JarvisCircle';
import { fetchCockpitBackends, runCockpit } from '../lib/api';
import type { CockpitBackendInfo, CockpitRunResponse } from '../lib/api';
import {
  loadCockpitPrefs,
  saveCockpitPrefs,
  type CockpitPrefs,
} from '../lib/cockpitPrefs';
import { useClapDetector } from '../lib/useClapDetector';
import { useVoice } from '../lib/useVoice';
import { bootRiff, playWakeCue } from '../lib/wakeSounds';

const QUICK_ACTIONS = [
  { label: 'Hello (ping)', command: 'hello' },
  { label: 'Good morning', command: 'good morning' },
  { label: 'Repo status', command: 'show repo status' },
  { label: 'Open browser', command: 'open browser' },
  { label: 'Play music', command: 'open music' },
  { label: 'Claude review', command: 'ask Claude to review diff' },
  { label: 'Codex next step', command: 'ask Codex to inspect next step' },
  { label: 'Lumo commit', command: 'ask Lumo to draft commit message' },
  { label: 'Latest docs', command: 'search latest docs' },
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
  const [prefs, setPrefs] = useState<CockpitPrefs>(() => loadCockpitPrefs());
  const [backends, setBackends] = useState<Record<string, CockpitBackendInfo> | null>(null);
  const [backendsError, setBackendsError] = useState<string>('');
  const [backendOverride, setBackendOverride] = useState<string>('');
  const [modelOverride, setModelOverride] = useState<string>('');
  const [statusLine, setStatusLine] = useState('');
  const stopContinuousRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    fetchCockpitBackends()
      .then((res) => {
        setBackends(res.backends);
        setBackendsError('');
      })
      .catch((exc) => {
        setBackends({});
        const msg = exc instanceof Error ? exc.message : 'request failed';
        setBackendsError(msg);
      });
  }, []);

  // Sync override defaults from saved prefs once backends arrive.
  useEffect(() => {
    if (!backends || backendOverride) return;
    const candidate = prefs.defaultBackend;
    if (candidate && backends[candidate]?.available) {
      setBackendOverride(candidate);
      setModelOverride(prefs.preferredModel[candidate] ?? '');
    }
  }, [backends, backendOverride, prefs]);

  const persist = (next: CockpitPrefs) => {
    setPrefs(next);
    saveCockpitPrefs(next);
  };

  const missing = useMemo(() => missingTools(result), [result]);
  const canRun = command.trim().length > 0 && !loading;

  const voice = useVoice(prefs.ttsEnabled);

  const submit = async (overrideCommand?: string) => {
    const next = (overrideCommand ?? command).trim();
    if (!next || loading) return;
    setLoading(true);
    setError('');
    setStatusLine(`Routing: ${next}`);
    try {
      const response = await runCockpit({
        command: next,
        repo_path: repoPath,
        dry_run: dryRun,
        backend: backendOverride || null,
        model: modelOverride || null,
      });
      setResult(response);
      setStatusLine(
        response.success
          ? `Routed to ${response.backend}${response.model ? ` (${response.model})` : ''}`
          : `Failed on ${response.backend}`,
      );
      if (prefs.ttsEnabled && response.success) {
        voice.speak(response.result.slice(0, 480));
      }
    } catch (exc) {
      setResult(null);
      const message = exc instanceof Error ? exc.message : 'Cockpit request failed';
      setError(message);
      setStatusLine(`Error: ${message}`);
    } finally {
      setLoading(false);
    }
  };

  const wakeAndListen = async () => {
    await playWakeCue();
    setStatusLine('Listening for command…');
    if (!voice.sttSupported) {
      setStatusLine(
        'Wake fired, but SpeechRecognition is unavailable in this browser. Use the mic button or type your command.',
      );
      return;
    }
    try {
      const transcript = await voice.listenOnce();
      if (!transcript) {
        setStatusLine('Heard nothing — try again.');
        return;
      }
      setCommand(transcript);
      await submit(transcript);
    } catch (exc) {
      const message = exc instanceof Error ? exc.message : 'STT error';
      setStatusLine(`STT: ${message}`);
    }
  };

  const clap = useClapDetector({
    enabled: prefs.wakeMode === 'clap',
    onClap: () => {
      void wakeAndListen();
    },
  });

  // Always-on wake: run a continuous SR loop and submit on the keyword "jarvis".
  useEffect(() => {
    if (prefs.wakeMode !== 'always' || !voice.sttSupported) {
      stopContinuousRef.current?.();
      stopContinuousRef.current = null;
      return;
    }
    const stop = voice.listenContinuous((text) => {
      const lower = text.toLowerCase();
      if (lower.includes('jarvis') || lower.includes('hey jar')) {
        const cleaned = text.replace(/(hey )?jarvis[,!:.]?\s*/i, '').trim();
        if (cleaned) {
          setCommand(cleaned);
          void submit(cleaned);
        } else {
          void playWakeCue();
          setStatusLine('Jarvis here — say a command.');
        }
      }
    });
    stopContinuousRef.current = stop;
    return () => {
      stop();
      stopContinuousRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefs.wakeMode, voice.sttSupported]);

  const jarvisState: JarvisState = loading
    ? 'thinking'
    : voice.speaking
      ? 'talking'
      : voice.listening || (prefs.wakeMode === 'clap' && clap.ready)
        ? 'listening'
        : 'idle';
  const jarvisLevel = voice.listening ? 0.75 : Math.min(1, clap.level * 2.2);

  const bootJarvis = async () => {
    setStatusLine('Booting Jarvis…');
    await bootRiff();
    // Speak after a beat so the riff doesn't fight the TTS.
    window.setTimeout(() => {
      if (prefs.ttsEnabled) {
        voice.speak('Welcome home, Sir. Jarvis online and at your service.');
      }
      setStatusLine('Jarvis online — at your service, Sir.');
    }, 1200);
  };

  const pushToTalk = async () => {
    if (!voice.sttSupported) {
      setStatusLine('SpeechRecognition is not supported in this browser.');
      return;
    }
    try {
      const transcript = await voice.listenOnce();
      if (transcript) {
        setCommand(transcript);
        await submit(transcript);
      }
    } catch (exc) {
      setStatusLine(exc instanceof Error ? `STT: ${exc.message}` : 'STT error');
    }
  };

  const backendEntries = useMemo(() => (backends ? Object.entries(backends) : []), [backends]);
  const modelOptions = backendOverride && backends ? backends[backendOverride]?.models ?? [] : [];

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
              CLI bridges only — no API keys, no Ollama required
            </div>
            <h1 className="text-xl font-semibold" style={{ color: 'var(--color-text)' }}>
              Filip Cockpit
            </h1>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
            <span className="inline-flex items-center gap-1.5">
              <ShieldCheck size={14} style={{ color: 'var(--color-success)' }} />
              {prefs.wakeMode === 'off' ? 'Wake off' : `Wake: ${prefs.wakeMode}`}
            </span>
            <span>{voice.sttSupported ? 'STT ready' : 'STT unsupported'}</span>
            <span>{prefs.ttsEnabled ? 'TTS on' : 'TTS off'}</span>
          </div>
        </header>

        {backendsError && (
          <div
            className="rounded-lg p-3 text-xs"
            style={{
              background: 'color-mix(in srgb, var(--color-error) 8%, transparent)',
              border: '1px solid color-mix(in srgb, var(--color-error) 32%, transparent)',
              color: 'var(--color-text-secondary)',
            }}
          >
            <div className="mb-1 flex items-center gap-1.5 font-medium" style={{ color: 'var(--color-error)' }}>
              <AlertTriangle size={14} /> Cockpit backend not reachable
            </div>
            <div>
              Could not reach <code>GET /v1/cockpit/backends</code> ({backendsError}). Start it with:
            </div>
            <pre
              className="mt-1.5 overflow-x-auto rounded px-2 py-1 text-[11px]"
              style={{ background: 'var(--color-bg-secondary)', color: 'var(--color-text)' }}
            >
              uv run --extra server python -m uvicorn openjarvis.server.cockpit_app:app --host 127.0.0.1 --port 8000
            </pre>
          </div>
        )}

        <section
          className="flex flex-col items-center gap-3 rounded-lg py-6"
          style={{
            background:
              'radial-gradient(circle at 50% 50%, color-mix(in srgb, var(--color-accent) 8%, transparent) 0%, transparent 65%), var(--color-surface)',
            border: '1px solid var(--color-border)',
          }}
        >
          <JarvisCircle state={jarvisState} level={jarvisLevel} size={220} />
          <div className="text-xs uppercase tracking-widest" style={{ color: 'var(--color-text-tertiary)' }}>
            {jarvisState === 'idle' && 'Standby'}
            {jarvisState === 'listening' && 'Listening'}
            {jarvisState === 'thinking' && 'Routing'}
            {jarvisState === 'talking' && 'Speaking'}
          </div>
        </section>

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
              onClick={() => void submit()}
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

        <section
          className="grid gap-3 rounded-lg p-4 md:grid-cols-3"
          style={{ background: 'var(--color-surface)', border: '1px solid var(--color-border)' }}
        >
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
              Backend override
            </span>
            <select
              value={backendOverride}
              onChange={(event) => {
                const next = event.target.value;
                setBackendOverride(next);
                if (next && backends) {
                  setModelOverride(prefs.preferredModel[next] ?? '');
                } else {
                  setModelOverride('');
                }
              }}
              className="rounded-lg px-3 py-2 text-sm outline-none"
              style={{
                background: 'var(--color-input-bg)',
                border: '1px solid var(--color-input-border)',
                color: 'var(--color-text)',
              }}
            >
              <option value="">Smart routing (auto)</option>
              {backendEntries.map(([key, info]) => (
                <option key={key} value={key} disabled={!info.available}>
                  {info.label}
                  {info.available ? '' : ' (unavailable)'}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
              Model
            </span>
            <select
              value={modelOverride}
              onChange={(event) => setModelOverride(event.target.value)}
              disabled={!backendOverride || modelOptions.length <= 1}
              className="rounded-lg px-3 py-2 text-sm outline-none"
              style={{
                background: 'var(--color-input-bg)',
                border: '1px solid var(--color-input-border)',
                color: 'var(--color-text)',
              }}
            >
              {(modelOptions.length ? modelOptions : ['']).map((model) => (
                <option key={model} value={model}>
                  {model === '' ? 'CLI session default' : model}
                </option>
              ))}
            </select>
          </label>

          <div className="flex flex-col gap-1.5">
            <span className="text-xs font-medium" style={{ color: 'var(--color-text-secondary)' }}>
              Wake
            </span>
            <div className="flex items-center gap-1">
              {(['off', 'clap', 'always'] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => persist({ ...prefs, wakeMode: mode })}
                  className="flex-1 rounded-lg px-2 py-2 text-xs"
                  style={{
                    background:
                      prefs.wakeMode === mode
                        ? 'var(--color-accent-subtle)'
                        : 'var(--color-bg-secondary)',
                    color:
                      prefs.wakeMode === mode
                        ? 'var(--color-text)'
                        : 'var(--color-text-secondary)',
                    border: '1px solid var(--color-border)',
                  }}
                >
                  {mode === 'off' ? 'Off' : mode === 'clap' ? 'Clap-clap' : 'Always-on'}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2">
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
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => persist({ ...prefs, ttsEnabled: !prefs.ttsEnabled })}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs"
              style={{
                background: prefs.ttsEnabled ? 'var(--color-accent-subtle)' : 'var(--color-bg-secondary)',
                color: prefs.ttsEnabled ? 'var(--color-text)' : 'var(--color-text-secondary)',
                border: '1px solid var(--color-border)',
              }}
              title={voice.ttsSupported ? 'Read replies aloud' : 'SpeechSynthesis unsupported'}
              disabled={!voice.ttsSupported}
            >
              {prefs.ttsEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
              {prefs.ttsEnabled ? 'TTS on' : 'TTS off'}
            </button>
            <button
              onClick={() => void pushToTalk()}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs"
              style={{
                background: voice.listening
                  ? 'color-mix(in srgb, var(--color-accent) 24%, transparent)'
                  : 'var(--color-bg-secondary)',
                color: 'var(--color-text)',
                border: '1px solid var(--color-border)',
              }}
              title={voice.sttSupported ? 'Push-to-talk' : 'SpeechRecognition unsupported'}
              disabled={!voice.sttSupported || loading}
            >
              {voice.listening ? <Mic size={14} /> : <MicOff size={14} />}
              {voice.listening ? 'Listening…' : 'Talk'}
            </button>
            <button
              onClick={() => void wakeAndListen()}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs"
              style={{
                background: 'var(--color-bg-secondary)',
                color: 'var(--color-text)',
                border: '1px solid var(--color-border)',
              }}
              title="Trigger wake manually"
            >
              <Ear size={14} />
              Wake
            </button>
            <button
              onClick={() => void bootJarvis()}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium"
              style={{
                background: 'color-mix(in srgb, var(--color-accent) 20%, transparent)',
                color: 'var(--color-text)',
                border: '1px solid var(--color-accent)',
              }}
              title="Play the Tony Stark boot riff and greeting"
            >
              <Sparkles size={14} />
              Boot Jarvis
            </button>
          </div>
        </section>

        {prefs.wakeMode === 'clap' && (
          <section
            className="flex items-center gap-3 rounded-lg p-3 text-xs"
            style={{
              background: 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-secondary)',
            }}
          >
            <Mic size={14} style={{ color: clap.ready ? 'var(--color-success)' : 'var(--color-text-tertiary)' }} />
            <span>
              {clap.error
                ? `Mic: ${clap.error}`
                : clap.ready
                  ? 'Listening for clap-clap'
                  : 'Requesting microphone…'}
            </span>
            <div
              className="ml-auto h-1.5 w-32 overflow-hidden rounded"
              style={{ background: 'var(--color-bg-tertiary)' }}
            >
              <div
                className="h-full rounded"
                style={{
                  width: `${Math.min(100, Math.round(clap.level * 200))}%`,
                  background: 'var(--color-accent)',
                  transition: 'width 80ms linear',
                }}
              />
            </div>
          </section>
        )}

        {statusLine && (
          <div
            className="rounded-lg px-3 py-2 text-xs"
            style={{
              background: 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
              color: 'var(--color-text-secondary)',
            }}
          >
            {statusLine}
          </div>
        )}

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
              {result?.model && (
                <div>
                  <dt className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>Model</dt>
                  <dd className="mt-1" style={{ color: 'var(--color-text-secondary)' }}>
                    {result.model}
                  </dd>
                </div>
              )}
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
