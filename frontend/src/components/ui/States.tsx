import { useEffect, useState, type ReactNode } from 'react';
import { useMessages } from '../../i18n/i18n.ts';
import { Button } from './Button.tsx';
import styles from './States.module.css';
import { cx } from '../../lib/cx.ts';

interface StateFrameProps {
  readonly icon: ReactNode;
  readonly title: ReactNode;
  readonly children?: ReactNode;
  readonly action?: ReactNode;
  readonly tone: 'neutral' | 'warning' | 'danger';
  readonly headingLevel?: 2 | 3 | undefined;
}

function StateFrame({ icon, title, children, action, tone, headingLevel = 3 }: StateFrameProps) {
  const Heading = `h${headingLevel}` as const;
  return (
    <>
      <span className={cx(styles.icon, styles[tone])} aria-hidden="true">
        {icon}
      </span>
      <div className={styles.text}>
        <Heading className={styles.title}>{title}</Heading>
        {children ? <div className={styles.description}>{children}</div> : null}
        {action ? <div className={styles.action}>{action}</div> : null}
      </div>
    </>
  );
}

export interface EmptyStateProps {
  readonly title: ReactNode;
  readonly children?: ReactNode;
  readonly action?: ReactNode;
  readonly headingLevel?: 2 | 3 | undefined;
}

/** Valid response, nothing to show. */
export function EmptyState({ title, children, action, headingLevel }: EmptyStateProps) {
  return (
    <div className={styles.state} data-state="empty">
      <StateFrame icon="∅" tone="neutral" title={title} action={action} headingLevel={headingLevel}>
        {children}
      </StateFrame>
    </div>
  );
}

export interface ErrorStateProps {
  readonly title: ReactNode;
  readonly children?: ReactNode;
  readonly onRetry?: (() => void) | undefined;
  readonly retryLabel?: string;
  /** Extra actions next to the retry button (e.g. "go back"). */
  readonly actions?: ReactNode;
  /** `unavailable` = honest absence (warning tone); `error` = failure (danger tone). */
  readonly variant?: 'unavailable' | 'error';
  readonly headingLevel?: 2 | 3 | undefined;
}

/** A failure or unavailable data source. Announced to assistive tech via role="alert". */
export function ErrorState({ title, children, onRetry, retryLabel, actions, variant = 'error', headingLevel }: ErrorStateProps) {
  const m = useMessages();
  const retry = onRetry ? (
    <Button variant="secondary" onClick={onRetry}>
      {retryLabel ?? m.states.retry}
    </Button>
  ) : null;
  return (
    <div className={styles.state} role="alert" data-state={variant}>
      <StateFrame
        icon={variant === 'unavailable' ? '!' : '×'}
        tone={variant === 'unavailable' ? 'warning' : 'danger'}
        title={title}
        headingLevel={headingLevel}
        action={
          retry || actions ? (
            <>
              {retry}
              {actions}
            </>
          ) : undefined
        }
      >
        {children}
      </StateFrame>
    </div>
  );
}

export interface LoadingStateProps {
  readonly label: string;
  /** Number of decorative skeleton rows rendered under the label. */
  readonly rows?: number;
  /** After this delay a "taking longer than usual" hint is added to the live region. */
  readonly slowAfterMs?: number;
}

/** Polite live region so screen readers hear the label (and the slow hint) once; the skeleton is decorative. */
export function LoadingState({ label, rows = 3, slowAfterMs = 4_000 }: LoadingStateProps) {
  const m = useMessages();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => {
      setSlow(true);
    }, slowAfterMs);
    return () => {
      clearTimeout(timer);
    };
  }, [slowAfterMs]);

  return (
    <div className={styles.loading} data-state="loading">
      <div role="status" aria-live="polite">
        <div className={styles.loadingLabel}>
          <span className={styles.spinner} aria-hidden="true" />
          <span>{label}</span>
        </div>
        {slow ? <p className={styles.slow}>{m.states.slow}</p> : null}
      </div>
      {rows > 0 ? (
        <div className={styles.skeletons} aria-hidden="true">
          {Array.from({ length: rows }, (_, index) => (
            <span key={index} className={styles.skeleton} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
