/**
 * Wake sound bank. Each cue is a pure-Web-Audio synthesized snippet so the
 * experience works out of the box without shipping any audio file.
 *
 * If the user drops one of `/wake.mp3`, `/wake-1.mp3` … `/wake-5.mp3` into
 * `frontend/public/`, those files are preferred and rotated through instead.
 *
 * All cues are short (≤ 1.2s), no looping, no streaming.
 */

type CueFn = (ctx: AudioContext) => void;

let lastIndex = -1;

const SYNTH_CUES: CueFn[] = [
  // 0. Classic Jarvis "online" three-note chord (G4 / C5 / E5).
  (ctx) => playChord(ctx, [392, 523.25, 659.25], 0.05, 1.0, 'sine'),
  // 1. Two-tone "boop" — quick rising fifth.
  (ctx) => playChord(ctx, [523.25, 784], 0.12, 0.6, 'triangle'),
  // 2. Quick triplet — three taps at A5.
  (ctx) => {
    [0, 0.09, 0.18].forEach((t) => playChord(ctx, [880], 0, 0.18, 'sine', t, 0.18));
  },
  // 3. Ascending arpeggio — C / E / G / C (CMaj).
  (ctx) => {
    [261.63, 329.63, 392, 523.25].forEach((freq, i) =>
      playChord(ctx, [freq], 0, 0.32, 'triangle', i * 0.08, 0.18),
    );
  },
  // 4. Mellow descending "doorbell" — E / C.
  (ctx) => {
    playChord(ctx, [659.25], 0, 0.4, 'sine', 0, 0.22);
    playChord(ctx, [523.25], 0, 0.6, 'sine', 0.18, 0.22);
  },
  // 5. Sci-fi sweep — short upward chirp 300 → 900 Hz.
  (ctx) => {
    const now = ctx.currentTime;
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0.001, now);
    gain.gain.exponentialRampToValueAtTime(0.18, now + 0.05);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.45);
    gain.connect(ctx.destination);
    const osc = ctx.createOscillator();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(300, now);
    osc.frequency.exponentialRampToValueAtTime(900, now + 0.4);
    osc.connect(gain);
    osc.start(now);
    osc.stop(now + 0.5);
  },
];

function playChord(
  ctx: AudioContext,
  freqs: number[],
  spacing: number,
  duration: number,
  type: OscillatorType = 'sine',
  startOffset = 0,
  peak = 0.25,
) {
  const t0 = ctx.currentTime + startOffset;
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0.001, t0);
  gain.gain.exponentialRampToValueAtTime(peak, t0 + 0.04);
  gain.gain.exponentialRampToValueAtTime(0.001, t0 + duration);
  gain.connect(ctx.destination);
  freqs.forEach((freq, i) => {
    const osc = ctx.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t0 + i * spacing);
    osc.connect(gain);
    osc.start(t0 + i * spacing);
    osc.stop(t0 + duration + 0.05);
  });
}

function pickIndex(): number {
  if (SYNTH_CUES.length === 1) return 0;
  let next = Math.floor(Math.random() * SYNTH_CUES.length);
  if (next === lastIndex) next = (next + 1) % SYNTH_CUES.length;
  lastIndex = next;
  return next;
}

/**
 * Try the user-supplied mp3 first (rotating through /wake.mp3 → /wake-5.mp3),
 * then fall back to one of the synthesized cues.
 */
export async function playWakeCue(): Promise<void> {
  const idx = pickIndex();
  const candidates = [`/wake-${idx}.mp3`, idx === 0 ? '/wake.mp3' : null].filter(
    (path): path is string => path !== null,
  );

  for (const path of candidates) {
    try {
      const audio = new Audio(path);
      audio.volume = 0.85;
      await audio.play();
      return;
    } catch {
      /* try next candidate */
    }
  }

  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    SYNTH_CUES[idx](ctx);
    window.setTimeout(() => ctx.close().catch(() => {}), 1500);
  } catch {
    /* nothing else to do — wake cue is non-critical */
  }
}

export const __testing = { SYNTH_CUES };

export interface BootRiffOptions {
  /** Peak playback volume (0..1). Use ~0.3 to play dimmed behind TTS. */
  volume?: number;
  /** Stop and fade the riff after this many ms. Defaults to 25000 (25s). */
  maxDurationMs?: number;
  /** Fade-out duration in ms. Defaults to 1500. */
  fadeMs?: number;
}

/**
 * Tony Stark boot riff — opening of "Should I Stay or Should I Go" by The
 * Clash.
 *
 * If the user drops `/boot.mp3` in `frontend/public/` we play that (real
 * recording — they supply it themselves; we cannot bundle the copyrighted
 * track). Otherwise we synthesize a recognizable approximation of the
 * iconic D / G-F / D intro pattern with detuned square waves through a
 * lowpass.
 *
 * Returns a `stop()` function so the caller can cut the riff early. The
 * riff also auto-fades + stops after `maxDurationMs`.
 */
export async function bootRiff(opts: BootRiffOptions = {}): Promise<() => void> {
  const volume = opts.volume ?? 0.9;
  const maxDurationMs = opts.maxDurationMs ?? 25_000;
  const fadeMs = opts.fadeMs ?? 1500;

  // Prefer the real recording if the user dropped one in /public.
  try {
    const audio = new Audio('/boot.mp3');
    audio.volume = volume;
    await audio.play();

    let stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      const start = audio.volume;
      const steps = Math.max(1, Math.floor(fadeMs / 50));
      let i = 0;
      const fade = window.setInterval(() => {
        i++;
        audio.volume = Math.max(0, start * (1 - i / steps));
        if (i >= steps) {
          window.clearInterval(fade);
          audio.pause();
        }
      }, 50);
    };
    window.setTimeout(stop, maxDurationMs);
    return stop;
  } catch {
    /* fall through to synth */
  }

  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const master = ctx.createGain();
    // Scale synth volume by the requested volume (default keeps prior behavior).
    const targetGain = 0.22 * (volume / 0.9);
    master.gain.value = targetGain;
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 2200;
    filter.Q.value = 0.5;
    filter.connect(master);
    master.connect(ctx.destination);

    // Power-chord stacks: root + fifth + octave with a slightly detuned
    // higher octave to fatten the guitar tone.
    // The intro riff in "Should I Stay or Should I Go" is essentially:
    //   D D D D | G F | D D | F G | D
    // played as chord stabs. Tempo ~113 BPM (~530 ms/beat); we use ~310 ms
    // for stab strums and longer holds on the resolves.
    const D: [number, number, number] = [146.83, 220.0, 293.66];
    const G: [number, number, number] = [196.0, 293.66, 392.0];
    const F: [number, number, number] = [174.61, 261.63, 349.23];

    const strums: Array<{ at: number; chord: [number, number, number]; dur: number; peak: number }> = [
      // Opening: four D stabs (Mick Jones's iconic intro).
      { at: 0.0, chord: D, dur: 0.28, peak: 0.7 },
      { at: 0.31, chord: D, dur: 0.28, peak: 0.7 },
      { at: 0.62, chord: D, dur: 0.28, peak: 0.7 },
      { at: 0.93, chord: D, dur: 0.45, peak: 0.75 },
      // Tension: G then F (the "Should I stay…" descent).
      { at: 1.55, chord: G, dur: 0.32, peak: 0.7 },
      { at: 1.95, chord: F, dur: 0.45, peak: 0.7 },
      // Resolve back to D.
      { at: 2.55, chord: D, dur: 0.32, peak: 0.7 },
      { at: 2.95, chord: D, dur: 0.45, peak: 0.75 },
      // Second tension: F → G.
      { at: 3.6, chord: F, dur: 0.32, peak: 0.7 },
      { at: 3.95, chord: G, dur: 0.32, peak: 0.7 },
      // Big closing D, held.
      { at: 4.4, chord: D, dur: 1.2, peak: 0.85 },
    ];

    const t0 = ctx.currentTime + 0.05;
    for (const s of strums) {
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.001, t0 + s.at);
      // Sharper attack for that chord-stab "thwack".
      g.gain.exponentialRampToValueAtTime(s.peak, t0 + s.at + 0.012);
      g.gain.exponentialRampToValueAtTime(0.001, t0 + s.at + s.dur);
      g.connect(filter);
      // Three oscillators per chord, plus one slightly detuned for fatness.
      for (let i = 0; i < s.chord.length; i++) {
        const f = s.chord[i];
        const osc = ctx.createOscillator();
        osc.type = 'square';
        osc.frequency.setValueAtTime(f, t0 + s.at);
        osc.connect(g);
        osc.start(t0 + s.at);
        osc.stop(t0 + s.at + s.dur + 0.05);
        // Detune the top voice by +6 cents to add chorus-like beat.
        if (i === s.chord.length - 1) {
          const osc2 = ctx.createOscillator();
          osc2.type = 'square';
          osc2.frequency.setValueAtTime(f * 1.0035, t0 + s.at);
          osc2.connect(g);
          osc2.start(t0 + s.at);
          osc2.stop(t0 + s.at + s.dur + 0.05);
        }
      }
    }
    // Natural synth riff length (~5.5s); cap at maxDurationMs with a fade.
    const naturalEndMs = 6000;
    const totalMs = Math.min(naturalEndMs, maxDurationMs);
    const fadeStart = Math.max(0, totalMs - fadeMs);

    let stopped = false;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      const now = ctx.currentTime;
      const safeFadeSec = Math.max(0.05, fadeMs / 1000);
      master.gain.cancelScheduledValues(now);
      master.gain.setValueAtTime(master.gain.value, now);
      master.gain.exponentialRampToValueAtTime(0.0001, now + safeFadeSec);
      window.setTimeout(() => ctx.close().catch(() => {}), fadeMs + 100);
    };

    if (fadeStart > 0 && fadeStart < naturalEndMs) {
      window.setTimeout(stop, fadeStart);
    } else {
      window.setTimeout(() => ctx.close().catch(() => {}), totalMs + 200);
    }
    return stop;
  } catch {
    /* ignore — boot riff is non-critical */
    return () => {};
  }
}
