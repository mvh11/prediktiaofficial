import { PRODUCT_CODE_TONES, PRODUCT_CODES, type ProductCode } from '../../content/productStatus.ts';
import { useMessages } from '../../i18n/i18n.ts';
import { StatusBadge } from './StatusBadge.tsx';
import styles from './ProductCodes.module.css';

/**
 * The product's status codes, shown verbatim (ABSTAIN, NOT_CERTIFIED,
 * WAIT_REAL_SOURCE, RESEARCH_ONLY) so they can be matched against project
 * documentation, each with a plain-language explanation.
 */
export function ProductCodes({ codes = PRODUCT_CODES }: { codes?: readonly ProductCode[] }) {
  const m = useMessages();
  return (
    <dl className={styles.codes}>
      {codes.map((code) => (
        <div key={code} className={styles.row}>
          <dt>
            <StatusBadge tone={PRODUCT_CODE_TONES[code]}>{code}</StatusBadge>
          </dt>
          <dd>{m.productCodes[code]}</dd>
        </div>
      ))}
    </dl>
  );
}
