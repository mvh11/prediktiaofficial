import type { ReactNode } from 'react';
import { useDocumentTitle } from '../../hooks/useDocumentTitle.ts';
import { useNavigationFocus } from '../../hooks/useNavigationFocus.ts';
import styles from './PageHeader.module.css';

export interface PageHeaderProps {
  readonly title: string;
  readonly eyebrow?: ReactNode;
  readonly lead?: ReactNode;
  readonly actions?: ReactNode;
  /** Overrides the `<title>`; defaults to the page title. */
  readonly documentTitle?: string;
}

/**
 * Page-level heading block. Every routed page renders exactly one <h1>; this
 * component provides it, sets the document title and receives focus after
 * client-side navigation.
 */
export function PageHeader({ title, eyebrow, lead, actions, documentTitle }: PageHeaderProps) {
  useDocumentTitle(documentTitle ?? title);
  const headingRef = useNavigationFocus<HTMLHeadingElement>();

  return (
    <header className={styles.header}>
      <div className="container">
        {eyebrow ? <p className={styles.eyebrow}>{eyebrow}</p> : null}
        <h1 ref={headingRef} className={styles.title} tabIndex={-1}>
          {title}
        </h1>
        {lead ? <div className={styles.lead}>{lead}</div> : null}
        {actions ? <div className={styles.actions}>{actions}</div> : null}
      </div>
    </header>
  );
}
