import { useEffect, useState } from 'react';
import { fetchServerInfo } from './api';

export type CockpitMode = 'loading' | 'cockpit' | 'full';

let cached: Exclude<CockpitMode, 'loading'> | null = null;
let inflight: Promise<Exclude<CockpitMode, 'loading'>> | null = null;

async function detect(): Promise<Exclude<CockpitMode, 'loading'>> {
  try {
    const info = await fetchServerInfo();
    const engine = (info as { engine?: string } | null)?.engine ?? '';
    return engine === 'cockpit' ? 'cockpit' : 'full';
  } catch {
    return 'full';
  }
}

export function useCockpitMode(): CockpitMode {
  const [mode, setMode] = useState<CockpitMode>(cached ?? 'loading');
  useEffect(() => {
    if (cached) {
      setMode(cached);
      return;
    }
    if (!inflight) inflight = detect();
    inflight.then((next) => {
      cached = next;
      setMode(next);
    });
  }, []);
  return mode;
}
