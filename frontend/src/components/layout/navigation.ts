export interface NavItem {
  readonly to: string;
  readonly label: string;
  /** Match only the exact path (used for the root route). */
  readonly end?: boolean;
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: '/', label: 'Overview', end: true },
  { to: '/fixtures', label: 'Fixtures' },
  { to: '/research', label: 'Research' },
  { to: '/methodology', label: 'Methodology' },
];
