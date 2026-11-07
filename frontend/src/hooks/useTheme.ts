import { useEffect, useState } from 'react';

export type ThemePreference = 'system' | 'light' | 'dark';

/** Must match the inline script in index.html that applies the theme before first paint. */
export const THEME_STORAGE_KEY = 'prediktia.theme';

function readStoredTheme(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

/** Applies a theme by setting `<html data-theme>`; "system" follows prefers-color-scheme. */
export function applyTheme(preference: ThemePreference): void {
  const root = document.documentElement;
  if (preference === 'system') delete root.dataset.theme;
  else root.dataset.theme = preference;
}

/** Viewer preference for light/dark appearance, persisted per browser (storage may be unavailable). */
export function useTheme(): [ThemePreference, (value: ThemePreference) => void] {
  const [preference, setPreference] = useState<ThemePreference>(readStoredTheme);

  useEffect(() => {
    applyTheme(preference);
    try {
      if (preference === 'system') window.localStorage.removeItem(THEME_STORAGE_KEY);
      else window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    } catch {
      // Private mode or blocked storage: the choice still applies for this visit.
    }
  }, [preference]);

  return [preference, setPreference];
}
