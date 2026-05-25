import { useEffect, useRef, useState } from 'react';

interface ClapDetectorOptions {
  enabled: boolean;
  onClap: () => void;
  threshold?: number;
  gapMs?: number;
  cooldownMs?: number;
  dynamicMult?: number;
  staticFloor?: number;
  minSpikeSeparationMs?: number;
}

interface ClapDetectorState {
  ready: boolean;
  error: string;
  level: number;
}

// Static absolute floor — we never trigger below this RMS no matter how
// quiet the room is, so mic hiss / fan hum can't fire a clap. Bumped from
// 0.06 → 0.10 after live use: fans, drawer scrapes, and key thuds were
// hitting the old floor.
const STATIC_FLOOR = 0.10;
// The dynamic threshold is `max(STATIC_FLOOR, baseline * MULT)`. With
// MULT=6, the detector requires a sample 6× louder than the rolling
// ambient noise floor — adapts to quiet rooms (Lamia2 idle ~0.005) and
// noisy ones (open mic in a café ~0.05) without retuning. Was 4×; lifted
// to suppress speech transients that occasionally double-fired.
const DYNAMIC_MULT = 6;
// Rolling baseline window in ms. 1.5s smooths out brief loud sounds but
// stays responsive to a new ambient level (window opening, AC kicking in).
const BASELINE_WINDOW_MS = 1500;
// Tight clap-clap gap (was 1500ms). Two real claps land within ~400-600ms.
// 750ms allows a slight uneven rhythm without trapping single loud noises.
const DEFAULT_GAP_MS = 750;
const DEFAULT_COOLDOWN_MS = 2500;
// Minimum spike separation — frame timing can register one clap as two
// when the RMS sample straddles two frames at the boundary. 120ms guard.
const MIN_SPIKE_SEPARATION_MS = 120;

/**
 * Detect two RMS spikes within `gapMs` (i.e. "clap clap"). Pure browser:
 * AudioContext + AnalyserNode; no audio is sent off-device.
 */
export function useClapDetector(options: ClapDetectorOptions): ClapDetectorState {
  const { enabled, onClap } = options;
  // `options.threshold`, if given, overrides the dynamic baseline and acts
  // as an absolute trigger — escape hatch for power users / tests.
  const staticOverride = options.threshold;
  const gapMs = options.gapMs ?? DEFAULT_GAP_MS;
  const cooldownMs = options.cooldownMs ?? DEFAULT_COOLDOWN_MS;
  const dynamicMult = options.dynamicMult ?? DYNAMIC_MULT;
  const staticFloor = options.staticFloor ?? STATIC_FLOOR;
  const minSpikeSeparationMs = options.minSpikeSeparationMs ?? MIN_SPIKE_SEPARATION_MS;

  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [level, setLevel] = useState(0);

  const onClapRef = useRef(onClap);
  onClapRef.current = onClap;

  useEffect(() => {
    if (!enabled) return;

    let cancelled = false;
    let audioCtx: AudioContext | null = null;
    let stream: MediaStream | null = null;
    let analyser: AnalyserNode | null = null;
    let raf = 0;
    let lastSpikeAt = 0;
    let lastFireAt = 0;
    let armed = true;

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        audioCtx = new Ctx();
        const source = audioCtx.createMediaStreamSource(stream);
        analyser = audioCtx.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        const buffer = new Uint8Array(analyser.fftSize);
        setReady(true);

        // Rolling baseline samples (RMS over last BASELINE_WINDOW_MS).
        // Samples below the static floor feed the baseline; loud samples
        // (claps) do not, so the baseline tracks ambient noise only.
        const baselineHistory: Array<{ at: number; rms: number }> = [];
        let baseline = 0;
        const tick = () => {
          if (cancelled || !analyser) return;
          analyser.getByteTimeDomainData(buffer);
          let sumSquares = 0;
          for (let i = 0; i < buffer.length; i++) {
            const v = (buffer[i] - 128) / 128;
            sumSquares += v * v;
          }
          const rms = Math.sqrt(sumSquares / buffer.length);
          setLevel(rms);

          const now = performance.now();
          const inCooldown = now - lastFireAt < cooldownMs;

          // Feed the baseline only with quiet samples — claps and speech
          // shouldn't pollute the ambient floor estimate.
          if (rms < staticFloor * 1.5) {
            baselineHistory.push({ at: now, rms });
            while (
              baselineHistory.length > 0
              && now - baselineHistory[0].at > BASELINE_WINDOW_MS
            ) {
              baselineHistory.shift();
            }
            if (baselineHistory.length > 0) {
              const sum = baselineHistory.reduce((a, b) => a + b.rms, 0);
              baseline = sum / baselineHistory.length;
            }
          }

          const dynamicThreshold = staticOverride
            ?? Math.max(staticFloor, baseline * dynamicMult);

          if (rms > dynamicThreshold && armed && !inCooldown) {
            // Reject spikes that arrive too close to the previous one —
            // a real clap takes 80-120ms of envelope, so anything tighter
            // is almost certainly the same impulse aliasing across frames.
            if (lastSpikeAt && now - lastSpikeAt < minSpikeSeparationMs) {
              raf = requestAnimationFrame(tick);
              return;
            }
            if (lastSpikeAt && now - lastSpikeAt <= gapMs) {
              lastFireAt = now;
              lastSpikeAt = 0;
              armed = false;
              setTimeout(() => {
                armed = true;
              }, 250);
              try {
                onClapRef.current();
              } catch {
                /* swallow consumer errors so the detector stays alive */
              }
            } else {
              lastSpikeAt = now;
              armed = false;
              setTimeout(() => {
                armed = true;
              }, 250);
            }
          } else if (lastSpikeAt && now - lastSpikeAt > gapMs) {
            lastSpikeAt = 0;
          }
          raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof Error ? exc.message : 'Microphone unavailable');
          setReady(false);
        }
      }
    };

    start();

    return () => {
      cancelled = true;
      if (raf) cancelAnimationFrame(raf);
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (audioCtx) audioCtx.close().catch(() => {});
      setReady(false);
      setLevel(0);
    };
  }, [enabled, staticOverride, gapMs, cooldownMs, dynamicMult, staticFloor, minSpikeSeparationMs]);

  return { ready, error, level };
}
