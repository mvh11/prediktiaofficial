import type { Messages } from '../../i18n/messages.es-CL.ts';

/** Canonical (Spanish) application paths. Legacy English paths redirect here (see app/legacyRedirects.tsx). */
export const PATHS = {
  home: '/',
  competitions: '/competiciones',
  fixtures: '/partidos',
  research: '/investigacion',
  methodology: '/metodologia',
} as const;

export function competitionPath(id: number): string {
  return `${PATHS.competitions}/${String(id)}`;
}

export function fixturePath(id: number): string {
  return `${PATHS.fixtures}/${String(id)}`;
}

export interface NavItem {
  readonly to: string;
  readonly label: keyof Messages['nav'];
  /** Match only the exact path (used for the root route). */
  readonly end?: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: PATHS.home, label: 'home', end: true },
  { to: PATHS.competitions, label: 'competitions' },
  { to: PATHS.fixtures, label: 'fixtures' },
  { to: PATHS.research, label: 'research' },
  { to: PATHS.methodology, label: 'methodology' },
];
