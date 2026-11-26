/**
 * Light or dark appearance.
 * Until someone picks one, the app follows the computer's own setting.
 * index.html applies the remembered choice before the page first paints, so there's no flash.
 */

import { useEffect } from 'preact/hooks';
import { getPref, hasPref, oneOf, usePref } from './prefs';

export type Theme = 'light' | 'dark';
const KEY = 'theme';
const valid = oneOf<Theme>('light', 'dark');
const systemQuery = () => matchMedia('(prefers-color-scheme: dark)');
const system = (): Theme => (systemQuery().matches ? 'dark' : 'light');

function apply(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

/** At start-up: apply the remembered theme, and follow the computer's setting until one is chosen. */
export function startTheme(): void {
  apply(getPref(KEY, system(), valid));
  systemQuery().addEventListener('change', () => {
    if (!hasPref(KEY)) apply(system());
  });
}

/** The current theme and a way to change (and remember) it. */
export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = usePref<Theme>(KEY, system(), valid);
  useEffect(() => apply(theme), [theme]);
  return [theme, setTheme];
}
