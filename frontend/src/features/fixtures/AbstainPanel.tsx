import { Link } from 'react-router';
import type { Fixture } from '../../api/schemas.ts';
import { PATHS } from '../../components/layout/navigation.ts';
import { ProductCodes } from '../../components/ui/ProductCodes.tsx';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useMessages } from '../../i18n/i18n.ts';
import styles from './Fixtures.module.css';

/**
 * "Contexto de investigación" for one fixture. The only model output this
 * build can render is ABSTAIN: while certified predictions are not authorized
 * (see PRODUCT_FLAGS in content/productStatus.ts) there is intentionally no
 * code path that renders a probability, price or recommendation.
 */
export function ResearchContext({ fixture }: { fixture: Fixture }) {
  const m = useMessages();
  const ft90 = fixture.fulltime_home !== null && fixture.fulltime_away !== null ? { home: fixture.fulltime_home, away: fixture.fulltime_away } : null;
  return (
    <section className={styles.research} aria-labelledby="research-heading">
      <h2 id="research-heading" className={styles.researchTitle}>
        {m.fixture.researchTitle}
      </h2>
      <p className="muted">{m.fixture.researchLead}</p>

      <section className={styles.abstain} aria-labelledby="abstain-heading">
        <div className={styles.abstainHeader}>
          <h3 id="abstain-heading" className={styles.abstainTitle}>
            {m.fixture.modelOutput}
          </h3>
          <StatusBadge tone="abstain">ABSTAIN</StatusBadge>
        </div>
        <p>{m.fixture.abstainBody}</p>
        <ProductCodes codes={['NOT_CERTIFIED', 'WAIT_REAL_SOURCE']} />
        <p className={styles.abstainLink}>
          <Link to={`${PATHS.methodology}#abstencion`}>{m.fixture.whyAbstain}</Link>
        </p>
      </section>

      <div className={styles.researchGrid}>
        <div className={styles.panel}>
          <h3 className={styles.panelTitle}>{m.fixture.ft90Title}</h3>
          <p>{ft90 ? m.fixture.ft90Available(ft90.home, ft90.away) : m.fixture.ft90Missing}</p>
        </div>
        <div className={styles.panel}>
          <h3 className={styles.panelTitle}>{m.fixture.gatesTitle}</h3>
          <ul className={styles.gateLinks}>
            <li>
              <Link to={`${PATHS.methodology}#requisitos`}>{m.fixture.gatesLink}</Link>
            </li>
            <li>
              <Link to={PATHS.research}>{m.fixture.researchLink}</Link>
            </li>
          </ul>
        </div>
      </div>
    </section>
  );
}
