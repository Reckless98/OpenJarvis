import { useCallback, useEffect, useRef, useState } from 'react';

// YouTube IFrame Player API — typed only enough for what we use.
interface YTPlayer {
  loadVideoById: (videoId: string) => void;
  playVideo: () => void;
  pauseVideo: () => void;
  stopVideo: () => void;
  setVolume: (volume: number) => void;
  getVolume: () => number;
  getVideoData?: () => { title?: string; video_id?: string };
}

interface YTNamespace {
  Player: new (
    elementOrId: HTMLElement | string,
    options: {
      height?: string | number;
      width?: string | number;
      videoId?: string;
      playerVars?: Record<string, string | number>;
      events?: {
        onReady?: (event: { target: YTPlayer }) => void;
        onStateChange?: (event: { data: number; target: YTPlayer }) => void;
        onError?: (event: { data: number }) => void;
      };
    },
  ) => YTPlayer;
  PlayerState: { ENDED: 0; PLAYING: 1; PAUSED: 2; BUFFERING: 3; CUED: 5 };
}

declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

const API_SRC = 'https://www.youtube.com/iframe_api';

let apiLoadPromise: Promise<YTNamespace> | null = null;

function loadYouTubeAPI(): Promise<YTNamespace> {
  if (apiLoadPromise) return apiLoadPromise;
  apiLoadPromise = new Promise((resolve, reject) => {
    if (window.YT && window.YT.Player) {
      resolve(window.YT);
      return;
    }
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${API_SRC}"]`);
    const prevHandler = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prevHandler?.();
      if (window.YT) resolve(window.YT);
      else reject(new Error('YT API loaded without YT namespace'));
    };
    if (!existing) {
      const tag = document.createElement('script');
      tag.src = API_SRC;
      tag.async = true;
      tag.onerror = () => reject(new Error('Failed to load YouTube IFrame API'));
      document.head.appendChild(tag);
    }
  });
  return apiLoadPromise;
}

export interface MusicPlayerController {
  /** True after a video has started playing in this session. */
  isPlaying: boolean;
  /** Video ID currently loaded ('' if nothing). */
  currentId: string;
  /** Best-effort title from `getVideoData()`; '' until known. */
  currentTitle: string;
  /** Error from the API, if any. */
  error: string;
  /** Element the hidden iframe should be mounted into. */
  containerRef: React.RefObject<HTMLDivElement | null>;
  /** Start (or switch to) playback of a YouTube video id. */
  play: (videoId: string, title?: string) => Promise<void>;
  /** Stop and unload the current video. */
  stop: () => void;
  /** Drop volume to `level` (0-100). Default 15. */
  duck: (level?: number) => void;
  /** Restore volume to `level` (0-100). Default 100. */
  restore: (level?: number) => void;
}

const DUCK_LEVEL = 15;
const FULL_LEVEL = 100;

/**
 * Lazy-mounts a hidden YouTube IFrame Player. The actual `<iframe>` lives
 * inside a div the caller renders via `containerRef`, so React owns the
 * element lifecycle.
 *
 * Calling `play()` will:
 *   - load the YT API once (idempotent global promise),
 *   - create the player on first call,
 *   - swap the video id thereafter (no re-creation, no autoplay-gesture loss).
 *
 * `duck()` / `restore()` are intended to be wrapped around TTS so the
 * music drops to a whisper while Jarvis is speaking, then ramps back up.
 */
export function useMusicPlayer(): MusicPlayerController {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const playerRef = useRef<YTPlayer | null>(null);
  const pendingPlayRef = useRef<{ id: string; title?: string } | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentId, setCurrentId] = useState('');
  const [currentTitle, setCurrentTitle] = useState('');
  const [error, setError] = useState('');

  const ensurePlayer = useCallback(async (videoId: string): Promise<YTPlayer> => {
    if (playerRef.current) return playerRef.current;
    if (!containerRef.current) {
      throw new Error('Music player container not mounted yet');
    }
    const YT = await loadYouTubeAPI();
    return new Promise<YTPlayer>((resolve, reject) => {
      const host = document.createElement('div');
      // Hidden by CSS in JarvisMusicPlayer; but also tiny in size to be safe.
      host.style.position = 'absolute';
      host.style.width = '1px';
      host.style.height = '1px';
      host.style.opacity = '0';
      host.style.pointerEvents = 'none';
      containerRef.current!.appendChild(host);
      const player = new YT.Player(host, {
        height: '0',
        width: '0',
        videoId,
        playerVars: {
          autoplay: 1,
          controls: 0,
          disablekb: 1,
          fs: 0,
          modestbranding: 1,
          playsinline: 1,
          rel: 0,
        },
        events: {
          onReady: ({ target }) => {
            playerRef.current = target;
            try {
              target.setVolume(FULL_LEVEL);
            } catch {
              /* ignore */
            }
            resolve(target);
          },
          onStateChange: ({ data, target }) => {
            if (!window.YT) return;
            const states = window.YT.PlayerState;
            if (data === states.PLAYING) {
              setIsPlaying(true);
              try {
                const info = target.getVideoData?.();
                if (info?.title) setCurrentTitle(info.title);
              } catch {
                /* ignore */
              }
            } else if (data === states.ENDED || data === states.PAUSED) {
              setIsPlaying(false);
            }
          },
          onError: ({ data }) => {
            setError(`YouTube player error ${data}`);
          },
        },
      });
      // Give YT 8s to fire onReady before we give up.
      window.setTimeout(() => {
        if (!playerRef.current) reject(new Error('YouTube player never became ready'));
      }, 8000);
    });
  }, []);

  const play = useCallback(
    async (videoId: string, title?: string) => {
      if (!videoId) return;
      pendingPlayRef.current = { id: videoId, title };
      setCurrentId(videoId);
      if (title) setCurrentTitle(title);
      try {
        const player = await ensurePlayer(videoId);
        // If the API was already ready, switch tracks instead of re-creating.
        if (playerRef.current && currentId && currentId !== videoId) {
          player.loadVideoById(videoId);
        }
        player.setVolume(FULL_LEVEL);
        player.playVideo();
      } catch (exc) {
        const msg = exc instanceof Error ? exc.message : 'music play failed';
        setError(msg);
      }
    },
    [currentId, ensurePlayer],
  );

  const stop = useCallback(() => {
    const player = playerRef.current;
    if (!player) {
      setIsPlaying(false);
      setCurrentId('');
      setCurrentTitle('');
      return;
    }
    try {
      player.stopVideo();
    } catch {
      /* ignore */
    }
    setIsPlaying(false);
    setCurrentId('');
    setCurrentTitle('');
  }, []);

  const duck = useCallback((level: number = DUCK_LEVEL) => {
    const player = playerRef.current;
    if (!player) return;
    try {
      player.setVolume(Math.max(0, Math.min(100, level)));
    } catch {
      /* ignore */
    }
  }, []);

  const restore = useCallback((level: number = FULL_LEVEL) => {
    const player = playerRef.current;
    if (!player) return;
    try {
      player.setVolume(Math.max(0, Math.min(100, level)));
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    return () => {
      const player = playerRef.current;
      if (player) {
        try {
          player.stopVideo();
        } catch {
          /* ignore */
        }
      }
    };
  }, []);

  return {
    isPlaying,
    currentId,
    currentTitle,
    error,
    containerRef,
    play,
    stop,
    duck,
    restore,
  };
}
