import { CAPABILITIES } from '../../content/productStatus.ts';
import { useMessages } from '../../i18n/i18n.ts';
import { StatusBadge } from './StatusBadge.tsx';
import styles from './CapabilityList.module.css';

/** Platform capability status as a description list (name → status + explanation). */
export function CapabilityList() {
  const m = useMessages();
  return (
    <dl className={styles.list}>
      {CAPABILITIES.map(({ id, tone }) => {
        const text = m.capabilities[id];
        return (
          <div key={id} className={styles.row} data-capability={id}>
            <dt className={styles.name}>{text.name}</dt>
            <dd className={styles.status}>
              <StatusBadge tone={tone} dot>
                {text.status}
              </StatusBadge>
            </dd>
            <dd className={styles.description}>{text.description}</dd>
          </div>
        );
      })}
    </dl>
  );
}
