const KEY = 'openjarvis-cockpit-prefs';

export interface CockpitPrefs {
  defaultBackend: string;
  preferredModel: Record<string, string>;
  wakeMode: 'off' | 'clap' | 'always';
  ttsEnabled: boolean;
}

const DEFAULTS: CockpitPrefs = {
  defaultBackend: '',
  preferredModel: {},
  wakeMode: 'off',
  ttsEnabled: false,
};

export function loadCockpitPrefs(): CockpitPrefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<CockpitPrefs>;
    return { ...DEFAULTS, ...parsed, preferredModel: { ...(parsed.preferredModel ?? {}) } };
  } catch {
    return { ...DEFAULTS };
  }
}

export function saveCockpitPrefs(prefs: CockpitPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    /* localStorage unavailable; silently ignore */
  }
}
