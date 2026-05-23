import { useEffect, useRef } from 'react';

export type JarvisState = 'idle' | 'listening' | 'thinking' | 'talking';

interface Props {
  state: JarvisState;
  /** 0..1 mic level for listening mode. */
  level?: number;
  size?: number;
}

const COLORS: Record<JarvisState, { ring: string; core: string; bars: string }> = {
  idle: { ring: '#3a86c8', core: '#5fb8ff', bars: '#3a86c8' },
  listening: { ring: '#22d3ee', core: '#67e8f9', bars: '#22d3ee' },
  thinking: { ring: '#f59e0b', core: '#fbbf24', bars: '#f59e0b' },
  talking: { ring: '#a78bfa', core: '#c4b5fd', bars: '#a78bfa' },
};

const NUM_BARS = 24;
const RADIUS = 70;
const INNER_RADIUS = 36;
const BAR_BASE = 6;
const BAR_MAX = 24;

/**
 * Animated arc-reactor / Jarvis circle. Pure SVG + canvas-less animation
 * driven by requestAnimationFrame. Bars react to `level` while listening and
 * oscillate procedurally while talking.
 */
export function JarvisCircle({ state, level = 0, size = 220 }: Props) {
  const colors = COLORS[state];
  const barsRef = useRef<SVGGElement | null>(null);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    const g = barsRef.current;
    if (!g) return;
    const start = performance.now();

    const animate = () => {
      const t = (performance.now() - start) / 1000;
      const bars = g.querySelectorAll<SVGRectElement>('rect');
      for (let i = 0; i < bars.length; i += 1) {
        let h: number;
        if (state === 'talking') {
          const phase = t * 6 + (i / NUM_BARS) * Math.PI * 4;
          h = BAR_BASE + (Math.sin(phase) * 0.5 + 0.5) * BAR_MAX;
        } else if (state === 'listening') {
          const base = level * BAR_MAX;
          const jitter = (Math.sin(t * 8 + i * 0.7) + 1) * 0.5 * 6;
          h = BAR_BASE + base + jitter;
        } else if (state === 'thinking') {
          const phase = t * 3 + (i / NUM_BARS) * Math.PI * 2;
          h = BAR_BASE + (Math.sin(phase) * 0.5 + 0.5) * 12;
        } else {
          // idle — gentle breathing
          const phase = t * 1.4 + (i / NUM_BARS) * Math.PI * 2;
          h = BAR_BASE + (Math.sin(phase) * 0.5 + 0.5) * 5;
        }
        bars[i].setAttribute('height', String(h));
      }
      rafRef.current = requestAnimationFrame(animate);
    };
    rafRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(rafRef.current);
  }, [state, level]);

  const cx = 100;
  const cy = 100;

  return (
    <svg
      viewBox="0 0 200 200"
      width={size}
      height={size}
      role="img"
      aria-label={`Jarvis ${state}`}
      style={{ display: 'block' }}
    >
      <defs>
        <radialGradient id="jarvis-core" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor={colors.core} stopOpacity="0.95" />
          <stop offset="60%" stopColor={colors.ring} stopOpacity="0.35" />
          <stop offset="100%" stopColor={colors.ring} stopOpacity="0" />
        </radialGradient>
        <filter id="jarvis-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="2.5" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Outer rotating ring */}
      <g style={{ transformOrigin: '100px 100px', animation: 'jarvis-spin 7s linear infinite' }}>
        <circle
          cx={cx}
          cy={cy}
          r={RADIUS + 16}
          fill="none"
          stroke={colors.ring}
          strokeOpacity="0.35"
          strokeWidth="1.4"
          strokeDasharray="2 6"
        />
      </g>

      {/* Counter-rotating inner thin ring */}
      <g style={{ transformOrigin: '100px 100px', animation: 'jarvis-spin-rev 11s linear infinite' }}>
        <circle
          cx={cx}
          cy={cy}
          r={RADIUS + 6}
          fill="none"
          stroke={colors.ring}
          strokeOpacity="0.55"
          strokeWidth="0.8"
          strokeDasharray="40 220"
        />
      </g>

      {/* Pulsing core */}
      <circle
        cx={cx}
        cy={cy}
        r={INNER_RADIUS}
        fill="url(#jarvis-core)"
        filter="url(#jarvis-glow)"
        style={{
          transformOrigin: '100px 100px',
          animation:
            state === 'idle'
              ? 'jarvis-pulse 3.4s ease-in-out infinite'
              : state === 'listening'
                ? 'jarvis-pulse 1.0s ease-in-out infinite'
                : state === 'talking'
                  ? 'jarvis-pulse 0.6s ease-in-out infinite'
                  : 'jarvis-pulse 1.6s ease-in-out infinite',
        }}
      />

      {/* Inner ring outline */}
      <circle
        cx={cx}
        cy={cy}
        r={INNER_RADIUS}
        fill="none"
        stroke={colors.core}
        strokeOpacity="0.85"
        strokeWidth="1.2"
      />

      {/* Bars around the rim */}
      <g ref={barsRef} filter="url(#jarvis-glow)">
        {Array.from({ length: NUM_BARS }).map((_, i) => {
          const angle = (i / NUM_BARS) * 360;
          return (
            <rect
              key={i}
              x={99}
              y={20}
              width={2}
              height={BAR_BASE}
              rx={1}
              fill={colors.bars}
              transform={`rotate(${angle} ${cx} ${cy})`}
            />
          );
        })}
      </g>

      <style>{`
        @keyframes jarvis-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes jarvis-spin-rev { from { transform: rotate(360deg); } to { transform: rotate(0deg); } }
        @keyframes jarvis-pulse {
          0%, 100% { transform: scale(1); opacity: 0.95; }
          50% { transform: scale(1.06); opacity: 1; }
        }
      `}</style>
    </svg>
  );
}
