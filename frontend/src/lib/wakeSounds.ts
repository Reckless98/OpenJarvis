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

/**
 * Tony Stark boot riff — opening of "Should I Stay or Should I Go" by The
 * Clash, synthesized live with three power chords (D / G / A) on a square
 * wave through a lowpass. About 5.5 seconds, fully offline, no copyrighted
 * audio.
 */
export async function bootRiff(): Promise<void> {
  try {
    const Ctx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    const master = ctx.createGain();
    master.gain.value = 0.18;
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.value = 1800;
    filter.connect(master);
    master.connect(ctx.destination);

    // D5 power chord, G5, A5 — root + fifth + octave (rough power-chord stack)
    const D: [number, number, number] = [146.83, 220, 293.66];
    const G: [number, number, number] = [196.0, 293.66, 392.0];
    const A: [number, number, number] = [110.0, 164.81, 220.0];

    // Riff: D D D D | G G | D | G | A A | D
    // strum times in seconds, chord per strum
    const strums: Array<{ at: number; chord: [number, number, number]; dur: number }> = [
      { at: 0.0, chord: D, dur: 0.35 },
      { at: 0.35, chord: D, dur: 0.35 },
      { at: 0.7, chord: D, dur: 0.35 },
      { at: 1.05, chord: D, dur: 0.5 },
      { at: 1.6, chord: G, dur: 0.35 },
      { at: 1.95, chord: G, dur: 0.5 },
      { at: 2.5, chord: D, dur: 0.5 },
      { at: 3.05, chord: G, dur: 0.5 },
      { at: 3.6, chord: A, dur: 0.35 },
      { at: 3.95, chord: A, dur: 0.4 },
      { at: 4.4, chord: D, dur: 1.0 },
    ];
    const t0 = ctx.currentTime + 0.05;
    for (const s of strums) {
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.001, t0 + s.at);
      g.gain.exponentialRampToValueAtTime(0.6, t0 + s.at + 0.015);
      g.gain.exponentialRampToValueAtTime(0.001, t0 + s.at + s.dur);
      g.connect(filter);
      for (const f of s.chord) {
        const osc = ctx.createOscillator();
        osc.type = 'square';
        osc.frequency.setValueAtTime(f, t0 + s.at);
        osc.connect(g);
        osc.start(t0 + s.at);
        osc.stop(t0 + s.at + s.dur + 0.05);
      }
    }
    window.setTimeout(() => ctx.close().catch(() => {}), 6500);
  } catch {
    /* ignore — boot riff is non-critical */
  }
}
