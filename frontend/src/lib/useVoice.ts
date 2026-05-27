import { useCallback, useEffect, useRef, useState } from 'react';

interface SRInstance {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult:
    | ((event: {
        results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }>;
        resultIndex: number;
      }) => void)
    | null;
  onerror: ((event: { error?: string }) => void) | null;
  onstart: (() => void) | null;
  onend: (() => void) | null;
}

function getSpeechRecognition(): (new () => SRInstance) | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SRInstance;
    webkitSpeechRecognition?: new () => SRInstance;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

// Known female TTS voice names across macOS, Chrome, Edge, Linux espeak/festival.
// We deny-list these so the cascade never lands on a female voice when *any*
// male voice — even a non-British one — is available. Jarvis is male.
const FEMALE_VOICE_DENY = /(female|woman|samantha|karen|moira|tessa|fiona|veena|victoria|allison|kate|serena|susan|fiona|amelie|amélie|google uk english female|google us english.+female|google english female|zira|hazel|catherine|linda|heather|jenny|aria|nova|emma|amy|joanna|kendra|kimberly|salli|ivy|kira|alva|ellen|laura|petra)/i;
const MALE_VOICE_HINT = /(male|man|daniel|oliver|arthur|george|ryan|thomas|brian|alex|fred|aaron|reed|mark|rocko|tom|joe|harry|gordon|david|james|matthew|jeremy|jorge|diego)/i;

/**
 * Pick the best available "Jarvis from Iron Man" voice — male, British,
 * formal. Falls back through a preference cascade and ultimately returns
 * `null` (let the browser pick the default voice).
 *
 * Voice list loads asynchronously in some browsers; if no voices are
 * loaded yet, returns null and the next speak() call will get one.
 */
function pickJarvisVoice(): SpeechSynthesisVoice | null {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return null;
  const allVoices = window.speechSynthesis.getVoices();
  if (!allVoices.length) return null;

  // Filter female voices out up front — never speak Jarvis as a woman.
  const voices = allVoices.filter((v) => !FEMALE_VOICE_DENY.test(`${v.name} ${v.voiceURI ?? ''}`));

  // Highest fidelity first — known male British voices on macOS/Chrome/Edge.
  const preferred = [
    /Daniel.*United Kingdom/i,
    /Daniel/i,
    /Google UK English Male/i,
    /Microsoft (George|Ryan|Thomas).*English.*United Kingdom/i,
    /Oliver/i,
    /Arthur/i,
    /en-GB.*Male/i,
  ];
  for (const pattern of preferred) {
    const match = voices.find((v) => pattern.test(`${v.name} ${v.lang}`));
    if (match) return match;
  }
  // Any explicitly-male en-GB voice.
  const maleGB = voices.find(
    (v) => v.lang?.toLowerCase().startsWith('en-gb') && MALE_VOICE_HINT.test(v.name),
  );
  if (maleGB) return maleGB;
  // Any en-GB voice that's not on the female denylist.
  const anyGB = voices.find((v) => v.lang?.toLowerCase().startsWith('en-gb'));
  if (anyGB) return anyGB;
  // Any explicitly-male English voice.
  const maleEN = voices.find(
    (v) => v.lang?.toLowerCase().startsWith('en') && MALE_VOICE_HINT.test(v.name),
  );
  if (maleEN) return maleEN;
  // Any English voice not on the female denylist.
  const anyEN = voices.find((v) => v.lang?.toLowerCase().startsWith('en'));
  if (anyEN) return anyEN;
  // Last resort: a non-female voice in any language is still better than a female Jarvis.
  return voices[0] ?? null;
}

export interface VoiceController {
  /** SpeechRecognition is available in this browser. */
  sttSupported: boolean;
  /** speechSynthesis is available in this browser. */
  ttsSupported: boolean;
  /** Currently capturing audio for STT. */
  listening: boolean;
  /** Currently speaking via TTS. */
  speaking: boolean;
  /** Live interim transcript while listening (cleared on finalize). */
  partial: string;
  /** Most recent error message (if any). */
  error: string;
  /** Start STT; resolves with the full transcript after a 2.5s silence gap. */
  listenOnce: (timeoutMs?: number, silenceMs?: number) => Promise<string>;
  /** Start continuous STT — calls `onTranscript` for each finalized utterance. Returns a stop fn. */
  listenContinuous: (onTranscript: (text: string) => void) => () => void;
  /** Stop any in-progress STT. */
  stopListening: () => void;
  /** Speak text via SpeechSynthesis (no-op if disabled or unsupported). */
  speak: (text: string) => void;
  /** Cancel any in-flight TTS. */
  cancelSpeak: () => void;
}

// GPT-Voice ergonomics: long enough for a full sentence with mid-thought pauses,
// short enough to feel responsive. 25s hard cap; finalize after 3.5s of silence
// (bumped from 2.5s in Phase 5 — Filip was getting cut off mid-thought).
const STT_TIMEOUT_MS = 25000;
const STT_SILENCE_MS = 3500;
// Grace period after TTS ends before we let the continuous listener re-arm.
// Prevents the tail of "playing now, sir" from bleeding into the next listen.
const POST_TTS_GRACE_MS = 300;

/**
 * Voice controller hardened for Chrome's quirks and tuned to GPT-Voice feel:
 * - `listenOnce` runs continuous + interim and finalizes on a client-side
 *   silence timer (2.5s by default) — so a mid-sentence pause doesn't cut you
 *   off after the first few words.
 * - Reuses a single `SpeechRecognition` instance across listen cycles
 *   (Chrome reprompts for mic permission if you recreate it on `file://`-ish
 *    contexts; reusing is also faster).
 * - Continuous mode handles Chrome's silent auto-stop by restarting on
 *   `onend` while a `shouldListen` flag is set.
 * - `not-allowed` / `service-not-allowed` errors disable auto-restart so we
 *   don't trap the user in a permission loop.
 * - Pre-existing `MediaStreamTrack`s from the clap detector are NOT touched —
 *   Chrome re-uses the origin's mic grant, so STT and clap share the mic
 *   without re-prompting.
 * - `speak()` toggles a `speakingRef`; listen calls short-circuit while TTS
 *   is active so Jarvis doesn't transcribe his own voice.
 */
export function useVoice(ttsEnabled: boolean): VoiceController {
  const SR = useRef<(new () => SRInstance) | null>(null);
  const sttSupportedRef = useRef(false);
  const ttsSupportedRef = useRef(false);
  const recognitionRef = useRef<SRInstance | null>(null);
  const isRunningRef = useRef(false);
  const speakingRef = useRef(false);
  const ttsGraceUntilRef = useRef(0);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [partial, setPartial] = useState('');
  const [error, setError] = useState('');

  if (SR.current === null) {
    SR.current = getSpeechRecognition();
    sttSupportedRef.current = SR.current !== null;
    ttsSupportedRef.current = typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  const getRecognition = useCallback((): SRInstance | null => {
    if (!SR.current) return null;
    if (recognitionRef.current) return recognitionRef.current;
    const sr = new SR.current();
    sr.lang = 'en-US';
    recognitionRef.current = sr;
    return sr;
  }, []);

  const stopListening = useCallback(() => {
    const sr = recognitionRef.current;
    if (!sr) return;
    try {
      sr.abort();
    } catch {
      /* ignore */
    }
    isRunningRef.current = false;
    setListening(false);
    setPartial('');
  }, []);

  const listenOnce = useCallback(
    (timeoutMs: number = STT_TIMEOUT_MS, silenceMs: number = STT_SILENCE_MS): Promise<string> => {
      const sr = getRecognition();
      if (!sr) return Promise.reject(new Error('SpeechRecognition not supported in this browser'));
      if (speakingRef.current || performance.now() < ttsGraceUntilRef.current) {
        return Promise.reject(new Error('STT blocked while Jarvis is speaking'));
      }
      if (isRunningRef.current) {
        try {
          sr.abort();
        } catch {
          /* ignore */
        }
      }
      return new Promise<string>((resolve, reject) => {
        sr.continuous = true;
        sr.interimResults = true;
        let settled = false;
        let finalBuffer = '';
        let interimText = '';
        let silenceTimer = 0;
        const clearSilenceTimer = () => {
          if (silenceTimer) {
            clearTimeout(silenceTimer);
            silenceTimer = 0;
          }
        };
        const finish = (fn: () => void) => {
          if (settled) return;
          settled = true;
          clearSilenceTimer();
          clearTimeout(hardTimer);
          isRunningRef.current = false;
          setListening(false);
          setPartial('');
          sr.onresult = null;
          sr.onerror = null;
          sr.onend = null;
          sr.onstart = null;
          try {
            sr.stop();
          } catch {
            /* ignore */
          }
          fn();
        };
        const finalize = () => {
          const text = (finalBuffer + ' ' + interimText).trim();
          if (text) {
            finish(() => resolve(text));
          } else {
            finish(() => reject(new Error('STT ended without result')));
          }
        };
        const armSilenceTimer = () => {
          clearSilenceTimer();
          silenceTimer = window.setTimeout(finalize, silenceMs);
        };
        const hardTimer = window.setTimeout(finalize, timeoutMs);
        sr.onstart = () => {
          isRunningRef.current = true;
          setListening(true);
          setPartial('');
          setError('');
          armSilenceTimer();
        };
        sr.onresult = (event) => {
          const results = Array.from(event.results);
          let newFinal = '';
          let newInterim = '';
          for (const r of results) {
            const text = r[0]?.transcript ?? '';
            if (r.isFinal) {
              newFinal += text + ' ';
            } else {
              newInterim += text + ' ';
            }
          }
          finalBuffer = newFinal.trim();
          interimText = newInterim.trim();
          setPartial((finalBuffer + ' ' + interimText).trim());
          armSilenceTimer();
        };
        sr.onerror = (event) => {
          const code = event.error ?? 'STT error';
          // `no-speech` just means the silence timer should win — don't reject.
          if (code === 'no-speech' || code === 'aborted') return;
          finish(() => reject(new Error(code)));
        };
        sr.onend = () => {
          // Chrome may auto-end after ~3-4s of silence. If we still have a
          // pending silence timer, let it decide whether to finalize.
          if (!settled && !silenceTimer) finalize();
        };
        try {
          sr.start();
        } catch (exc) {
          finish(() => reject(exc instanceof Error ? exc : new Error('STT start failed')));
        }
      });
    },
    [getRecognition],
  );

  const listenContinuous = useCallback(
    (onTranscript: (text: string) => void) => {
      const sr = getRecognition();
      if (!sr) {
        setError('SpeechRecognition not supported in this browser');
        return () => {};
      }
      let shouldListen = true;

      const start = () => {
        if (!shouldListen || isRunningRef.current) return;
        if (speakingRef.current || performance.now() < ttsGraceUntilRef.current) {
          // Wait for TTS + grace to clear before re-arming.
          window.setTimeout(start, 200);
          return;
        }
        sr.continuous = true;
        sr.interimResults = true;
        try {
          sr.start();
        } catch {
          /* Chrome throws InvalidStateError if start() races onend — ignored. */
        }
      };

      sr.onstart = () => {
        isRunningRef.current = true;
        setListening(true);
        setError('');
      };
      sr.onresult = (event) => {
        const idx = event.resultIndex ?? 0;
        const results = Array.from(event.results).slice(idx);
        let finalText = '';
        let interimText = '';
        for (const r of results) {
          const text = r[0]?.transcript ?? '';
          if (r.isFinal) finalText += text + ' ';
          else interimText += text + ' ';
        }
        const combined = (finalText + interimText).trim();
        if (combined) setPartial(combined);
        if (finalText.trim()) {
          setPartial('');
          onTranscript(finalText.trim());
        }
      };
      sr.onerror = (event) => {
        const code = event.error ?? 'STT error';
        // Silent errors that just mean "restart" — don't surface or stop.
        if (code === 'no-speech' || code === 'aborted') return;
        setError(code);
        if (code === 'not-allowed' || code === 'service-not-allowed') {
          shouldListen = false;
        }
      };
      sr.onend = () => {
        isRunningRef.current = false;
        setListening(false);
        // Chrome auto-stops after ~3-4s of silence. Restart immediately if
        // the consumer still wants to listen (and TTS isn't active).
        if (shouldListen) {
          window.setTimeout(start, 80);
        }
      };

      start();

      return () => {
        shouldListen = false;
        try {
          sr.abort();
        } catch {
          /* ignore */
        }
        isRunningRef.current = false;
        setListening(false);
        setPartial('');
      };
    },
    [getRecognition],
  );

  const cancelSpeak = useCallback(() => {
    if (!ttsSupportedRef.current) return;
    try {
      window.speechSynthesis.cancel();
    } catch {
      /* ignore */
    }
    speakingRef.current = false;
    ttsGraceUntilRef.current = performance.now() + POST_TTS_GRACE_MS;
    setSpeaking(false);
  }, []);

  const speak = useCallback(
    (text: string) => {
      if (!ttsEnabled || !ttsSupportedRef.current || !text) return;
      try {
        const utterance = new SpeechSynthesisUtterance(text);
        const voice = pickJarvisVoice();
        if (voice) utterance.voice = voice;
        utterance.lang = voice?.lang ?? 'en-GB';
        utterance.rate = 0.96;
        utterance.pitch = 0.9;
        utterance.onstart = () => {
          speakingRef.current = true;
          setSpeaking(true);
        };
        const finishSpeaking = () => {
          speakingRef.current = false;
          ttsGraceUntilRef.current = performance.now() + POST_TTS_GRACE_MS;
          setSpeaking(false);
        };
        utterance.onend = finishSpeaking;
        utterance.onerror = finishSpeaking;
        // Mark speaking immediately so any racing listen call short-circuits
        // before the browser actually fires onstart.
        speakingRef.current = true;
        setSpeaking(true);
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
      } catch (exc) {
        speakingRef.current = false;
        setError(exc instanceof Error ? exc.message : 'TTS failed');
        setSpeaking(false);
      }
    },
    [ttsEnabled],
  );

  useEffect(() => () => stopListening(), [stopListening]);

  // Chrome populates voices asynchronously; nudge once on mount so the
  // first speak() reliably picks the Jarvis voice instead of the default.
  useEffect(() => {
    if (!ttsSupportedRef.current) return;
    const synth = window.speechSynthesis;
    synth.getVoices();
    const handler = () => synth.getVoices();
    synth.addEventListener?.('voiceschanged', handler);
    return () => synth.removeEventListener?.('voiceschanged', handler);
  }, []);

  return {
    sttSupported: sttSupportedRef.current,
    ttsSupported: ttsSupportedRef.current,
    listening,
    speaking,
    partial,
    error,
    listenOnce,
    listenContinuous,
    stopListening,
    speak,
    cancelSpeak,
  };
}
