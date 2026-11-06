import { Link } from 'react-router';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import styles from './Fixtures.module.css';

/**
 * The only "model output" this build can render. While certified predictions
 * are not authorized (see PRODUCT_FLAGS in content/productStatus.ts), every
 * fixture resolves to ABSTAIN. There is intentionally no code path that
 * renders a probability, price or recommendation.
 */
export function AbstainPanel({ headingLevel = 2 }: { headingLevel?: 2 | 3 }) {
  const Heading = `h${headingLevel}` as const;
  return (
    <section className={styles.abstain} aria-labelledby="abstain-heading">
      <div className={styles.abstainHeader}>
        <Heading id="abstain-heading" className={styles.abstainTitle}>
          Model output
        </Heading>
        <StatusBadge tone="abstain">ABSTAIN</StatusBadge>
      </div>
      <p>
        PREDIKTIA is not authorized to publish certified predictions. Its models are research only, so no probability,
        odds, expected value or recommendation is shown for this match.
      </p>
      <p className={styles.abstainLink}>
        <Link to="/methodology#abstain">Why PREDIKTIA abstains</Link>
      </p>
    </section>
  );
}
