import type { ReactNode } from 'react';
import styles from './Card.module.css';
import { cx } from '../../lib/cx.ts';

export interface CardProps {
  readonly title?: ReactNode;
  /** Heading level for `title`; keeps the document outline correct per page. */
  readonly headingLevel?: 2 | 3 | 4;
  readonly eyebrow?: ReactNode;
  readonly footer?: ReactNode;
  readonly as?: 'section' | 'article' | 'div' | 'li';
  readonly variant?: 'default' | 'muted' | 'accent';
  readonly className?: string;
  readonly children?: ReactNode;
}

export function Card({ title, headingLevel = 3, eyebrow, footer, as: Tag = 'div', variant = 'default', className, children }: CardProps) {
  const Heading = `h${headingLevel}` as const;
  return (
    <Tag className={cx(styles.card, styles[variant], className)}>
      {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
      {title ? <Heading className={styles.title}>{title}</Heading> : null}
      {children ? <div className={styles.body}>{children}</div> : null}
      {footer ? <div className={styles.footer}>{footer}</div> : null}
    </Tag>
  );
}
