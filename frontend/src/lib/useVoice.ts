import { useCallback, useEffect, useRef, useState } from 'react';

interface SRInstance {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>>; resultIndex: number }) => void) | null;
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
  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return null;

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
  // Any en-GB voice.
  const anyGB = voices.find((v) => v.lang?.toLowerCase().startsWith('en-gb'));
  if (anyGB) return anyGB;
  // Last resort: any English voice.
  return voices.find((v) => v.lang?.toLowerCase().startsWith('en')) ?? null;
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
  /** Most recent error message (if any). */
  error: string;
  /** Start STT; resolves with the next transcript. Rejects on error/timeout. */
  listenOnce: (timeoutMs?: number) => Promise<string>;
  /** Start continuous STT — calls `onTranscript` for each utterance. Returns a stop fn. */
  listenContinuous: (onTranscript: (text: string) => void) => () => void;
  /** Stop any in-progress STT. */
  stopListening: () => void;
  /** Speak text via SpeechSynthesis (no-op if disabled or unsupported). */
  speak: (text: string) => void;
  /** Cancel any in-flight TTS. */
  cancelSpeak: () => void;
}

const STT_TIMEOUT_MS = 8000;

/**
 * Voice controller hardened for Chrome's quirks:
 * - Reuse a single `SpeechRecognition` instance across listen cycles
 *   (Chrome reprompts for mic permission if you recreate it on `file://`-ish
 *    contexts; reusing is also faster).
 * - Continuous mode handles Chrome's silent auto-stop by restarting on
 *   `onend` while a `shouldListen` flag is set.
 * - `not-allowed` / `service-not-allowed` errors disable auto-restart so we
 *   don't trap the user in a permission loop.
 * - Pre-existing `MediaStreamTrack`s from the clap detector are NOT touched —
 *   Chrome re-uses the origin's mic grant, so STT and clap share the mic
 *   without re-prompting.
 */
export function useVoice(ttsEnabled: boolean): VoiceController {
  const SR = useRef<(new () => SRInstance) | null>(null);
  const sttSupportedRef = useRef(false);
  const ttsSupportedRef = useRef(false);
  const recognitionRef = useRef<SRInstance | null>(null);
  const isRunningRef = useRef(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
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
  }, []);

  const listenOnce = useCallback(
    (timeoutMs: number = STT_TIMEOUT_MS): Promise<string> => {
      const sr = getRecognition();
      if (!sr) return Promise.reject(new Error('SpeechRecognition not supported in this browser'));
      if (isRunningRef.current) {
        try {
          sr.abort();
        } catch {
          /* ignore */
        }
      }
      return new Promise<string>((resolve, reject) => {
        sr.continuous = false;
        sr.interimResults = false;
        let settled = false;
        const finish = (fn: () => void) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          isRunningRef.current = false;
          setListening(false);
          sr.onresult = null;
          sr.onerror = null;
          sr.onend = null;
          sr.onstart = null;
          fn();
        };
        const timer = window.setTimeout(() => {
          try {
            sr.stop();
          } catch {
            /* ignore */
          }
          finish(() => reject(new Error('STT timeout')));
        }, timeoutMs);
        sr.onstart = () => {
          isRunningRef.current = true;
          setListening(true);
          setError('');
        };
        sr.onresult = (event) => {
          const transcript = Array.from(event.results)
            .map((r) => r[0]?.transcript ?? '')
            .join(' ')
            .trim();
          finish(() => resolve(transcript));
        };
        sr.onerror = (event) => {
          const code = event.error ?? 'STT error';
          finish(() => reject(new Error(code)));
        };
        sr.onend = () => {
          if (!settled) finish(() => reject(new Error('STT ended without result')));
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
        sr.continuous = true;
        sr.interimResults = false;
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
        const text = results
          .map((r) => r[0]?.transcript ?? '')
          .join(' ')
          .trim();
        if (text) onTranscript(text);
      };
      sr.onerror = (event) => {
        const code = event.error ?? 'STT error';
        setError(code);
        if (code === 'not-allowed' || code === 'service-not-allowed') {
          shouldListen = false;
        }
      };
      sr.onend = () => {
        isRunningRef.current = false;
        setListening(false);
        // Chrome auto-stops after ~3-4s of silence. Restart immediately if
        // the consumer still wants to listen.
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
        utterance.onstart = () => setSpeaking(true);
        utterance.onend = () => setSpeaking(false);
        utterance.onerror = () => setSpeaking(false);
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
      } catch (exc) {
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
    error,
    listenOnce,
    listenContinuous,
    stopListening,
    speak,
    cancelSpeak,
  };
}
