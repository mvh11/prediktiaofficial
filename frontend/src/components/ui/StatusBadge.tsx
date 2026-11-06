import type { ReactNode } from 'react';
import styles from './StatusBadge.module.css';
import { cx } from '../../lib/cx.ts';

export type BadgeTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'abstain';

export interface StatusBadgeProps {
  readonly tone?: BadgeTone;
  readonly children: ReactNode;
  /** Optional dot marker; purely decorative, the text always carries the meaning. */
  readonly dot?: boolean;
}

export function StatusBadge({ tone = 'neutral', dot = false, children }: StatusBadgeProps) {
  return (
    <span className={cx(styles.badge, styles[tone])} data-tone={tone}>
      {dot ? <span className={styles.dot} aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
