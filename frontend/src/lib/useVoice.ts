import { useCallback, useEffect, useRef, useState } from 'react';

interface SRInstance {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
}

function getSpeechRecognition(): (new () => SRInstance) | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SRInstance;
    webkitSpeechRecognition?: new () => SRInstance;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export interface VoiceController {
  /** SpeechRecognition is available in this browser. */
  sttSupported: boolean;
  /** speechSynthesis is available in this browser. */
  ttsSupported: boolean;
  /** Currently capturing audio for STT. */
  listening: boolean;
  /** Most recent error message (if any). */
  error: string;
  /** Start STT; resolves with the transcript. Rejects on error/timeout. */
  listenOnce: (timeoutMs?: number) => Promise<string>;
  /** Stop any in-progress STT. */
  stopListening: () => void;
  /** Speak text via SpeechSynthesis (no-op if disabled or unsupported). */
  speak: (text: string) => void;
}

const STT_TIMEOUT_MS = 8000;

export function useVoice(ttsEnabled: boolean): VoiceController {
  const SR = useRef<(new () => SRInstance) | null>(null);
  const sttSupported = useRef(false);
  const ttsSupported = useRef(false);
  const recognitionRef = useRef<SRInstance | null>(null);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState('');

  if (SR.current === null) {
    SR.current = getSpeechRecognition();
    sttSupported.current = SR.current !== null;
    ttsSupported.current = typeof window !== 'undefined' && 'speechSynthesis' in window;
  }

  const stopListening = useCallback(() => {
    const sr = recognitionRef.current;
    if (sr) {
      try {
        sr.abort();
      } catch {
        /* ignore */
      }
      recognitionRef.current = null;
    }
    setListening(false);
  }, []);

  const listenOnce = useCallback(
    (timeoutMs: number = STT_TIMEOUT_MS): Promise<string> => {
      if (!SR.current) return Promise.reject(new Error('SpeechRecognition not supported in this browser'));
      stopListening();
      return new Promise<string>((resolve, reject) => {
        const Ctor = SR.current;
        if (!Ctor) {
          reject(new Error('SpeechRecognition not supported'));
          return;
        }
        const sr = new Ctor();
        sr.continuous = false;
        sr.interimResults = false;
        sr.lang = 'en-US';
        let settled = false;
        const finish = (fn: () => void) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          recognitionRef.current = null;
          setListening(false);
          fn();
        };
        const timer = setTimeout(() => {
          try {
            sr.stop();
          } catch {
            /* ignore */
          }
          finish(() => reject(new Error('STT timeout')));
        }, timeoutMs);
        sr.onresult = (event) => {
          const transcript = Array.from(event.results)
            .map((r) => r[0]?.transcript ?? '')
            .join(' ')
            .trim();
          finish(() => resolve(transcript));
        };
        sr.onerror = (event) => {
          finish(() => reject(new Error(event.error ?? 'STT error')));
        };
        sr.onend = () => {
          if (!settled) finish(() => reject(new Error('STT ended without result')));
        };
        recognitionRef.current = sr;
        try {
          sr.start();
          setListening(true);
          setError('');
        } catch (exc) {
          finish(() => reject(exc instanceof Error ? exc : new Error('STT start failed')));
        }
      });
    },
    [stopListening],
  );

  const speak = useCallback(
    (text: string) => {
      if (!ttsEnabled || !ttsSupported.current || !text) return;
      try {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1.05;
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : 'TTS failed');
      }
    },
    [ttsEnabled],
  );

  useEffect(() => () => stopListening(), [stopListening]);

  return {
    sttSupported: sttSupported.current,
    ttsSupported: ttsSupported.current,
    listening,
    error,
    listenOnce,
    stopListening,
    speak,
  };
}
