import { Link } from 'react-router';
import { useMessages } from '../../i18n/i18n.ts';
import styles from './Breadcrumbs.module.css';

export interface Crumb {
  readonly label: string;
  /** Omitted for the current page (always the last crumb). */
  readonly to?: string;
}

/** Where the page sits in Inicio → Competiciones → Partidos → Detalle. The last item is the current page. */
export function Breadcrumbs({ items }: { items: readonly Crumb[] }) {
  const m = useMessages();
  return (
    <nav aria-label={m.layout.breadcrumbs} className={styles.breadcrumbs}>
      <ol className={styles.list}>
        {items.map((item, index) => {
          const current = index === items.length - 1;
          return (
            <li key={`${String(index)}-${item.label}`} className={styles.item}>
              {current || item.to === undefined ? (
                <span aria-current={current ? 'page' : undefined} className={styles.current}>
                  {item.label}
                </span>
              ) : (
                <Link to={item.to}>{item.label}</Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
