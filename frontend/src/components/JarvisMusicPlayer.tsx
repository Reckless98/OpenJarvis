import { Music, X } from 'lucide-react';
import type { MusicPlayerController } from '../lib/useMusicPlayer';

interface JarvisMusicPlayerProps {
  player: MusicPlayerController;
}

/**
 * Hidden YouTube IFrame host + a small visible "now playing" pill.
 *
 * The iframe lives in `containerRef` so the YT API can mount its frame there
 * the first time `useMusicPlayer().play()` is called. The pill on the right
 * shows what's playing and exposes a Stop button.
 */
export function JarvisMusicPlayer({ player }: JarvisMusicPlayerProps) {
  return (
    <>
      <div
        ref={player.containerRef}
        aria-hidden="true"
        style={{
          position: 'fixed',
          left: '-9999px',
          top: '-9999px',
          width: 1,
          height: 1,
          overflow: 'hidden',
          pointerEvents: 'none',
        }}
      />
      {player.isPlaying && (
        <div
          className="inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs"
          style={{
            background: 'color-mix(in srgb, var(--color-accent) 14%, transparent)',
            border: '1px solid color-mix(in srgb, var(--color-accent) 40%, transparent)',
            color: 'var(--color-text)',
            maxWidth: 320,
          }}
          title={player.currentTitle || player.currentId}
        >
          <Music size={12} style={{ color: 'var(--color-accent)' }} />
          <span className="truncate">
            {player.currentTitle ? player.currentTitle : `Playing ${player.currentId}`}
          </span>
          <button
            onClick={() => player.stop()}
            className="ml-1 rounded p-0.5"
            style={{ color: 'var(--color-text-secondary)' }}
            title="Stop music"
          >
            <X size={12} />
          </button>
        </div>
      )}
    </>
  );
}
