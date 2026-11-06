import { CAPABILITIES } from '../../content/productStatus.ts';
import { StatusBadge } from './StatusBadge.tsx';
import styles from './CapabilityList.module.css';

/** Platform capability status as a description list (name → status + explanation). */
export function CapabilityList() {
  return (
    <dl className={styles.list}>
      {CAPABILITIES.map((capability) => (
        <div key={capability.id} className={styles.row} data-capability={capability.id}>
          <dt className={styles.name}>{capability.name}</dt>
          <dd className={styles.status}>
            <StatusBadge tone={capability.tone} dot>
              {capability.status}
            </StatusBadge>
          </dd>
          <dd className={styles.description}>{capability.description}</dd>
        </div>
      ))}
    </dl>
  );
}
