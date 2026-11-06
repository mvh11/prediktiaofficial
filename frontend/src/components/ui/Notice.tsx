import type { ReactNode } from 'react';
import styles from './Notice.module.css';
import { cx } from '../../lib/cx.ts';

export interface NoticeProps {
  readonly tone?: 'info' | 'warning' | 'abstain';
  readonly title?: ReactNode;
  readonly children: ReactNode;
}

/** Static, non-urgent callout (deliberately not a live region or landmark). */
export function Notice({ tone = 'info', title, children }: NoticeProps) {
  return (
    <div className={cx(styles.notice, styles[tone])} data-tone={tone}>
      {title ? <p className={styles.title}>{title}</p> : null}
      <div className={styles.body}>{children}</div>
    </div>
  );
}
