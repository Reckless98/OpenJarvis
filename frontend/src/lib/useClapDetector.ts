import { useEffect, useRef, useState } from 'react';

interface ClapDetectorOptions {
  enabled: boolean;
  onClap: () => void;
  threshold?: number;
  gapMs?: number;
  cooldownMs?: number;
}

interface ClapDetectorState {
  ready: boolean;
  error: string;
  level: number;
}

const DEFAULT_THRESHOLD = 0.45;
const DEFAULT_GAP_MS = 1500;
const DEFAULT_COOLDOWN_MS = 2500;

/**
 * Detect two RMS spikes within `gapMs` (i.e. "clap clap"). Pure browser:
 * AudioContext + AnalyserNode; no audio is sent off-device.
 */
export function useClapDetector(options: ClapDetectorOptions): ClapDetectorState {
  const { enabled, onClap } = options;
  const threshold = options.threshold ?? DEFAULT_THRESHOLD;
  const gapMs = options.gapMs ?? DEFAULT_GAP_MS;
  const cooldownMs = options.cooldownMs ?? DEFAULT_COOLDOWN_MS;

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
          if (rms > threshold && armed && !inCooldown) {
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
  }, [enabled, threshold, gapMs, cooldownMs]);

  return { ready, error, level };
}
