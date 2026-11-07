import { PATHS } from '../components/layout/navigation.ts';
import { ButtonLink } from '../components/ui/Button.tsx';
import { CapabilityList } from '../components/ui/CapabilityList.tsx';
import { Card } from '../components/ui/Card.tsx';
import { ProductCodes } from '../components/ui/ProductCodes.tsx';
import { DataServiceStatus } from '../features/system/DataServiceStatus.tsx';
import { SITE_NAME, useDocumentTitle } from '../hooks/useDocumentTitle.ts';
import { useNavigationFocus } from '../hooks/useNavigationFocus.ts';
import { useMessages } from '../i18n/i18n.ts';
import styles from './HomePage.module.css';
import { cx } from '../lib/cx.ts';

/** Destination of each journey step, in the order of `messages.home.journey`. */
const JOURNEY_PATHS = [PATHS.competitions, PATHS.fixtures, PATHS.fixtures, PATHS.research] as const;

export function HomePage() {
  const m = useMessages();
  useDocumentTitle(SITE_NAME);
  const headingRef = useNavigationFocus<HTMLHeadingElement>();
  return (
    <>
      <section className={styles.hero} aria-labelledby="hero-title">
        <div className={cx('container', styles.heroInner)}>
          <p className={styles.kicker}>{m.home.kicker}</p>
          <h1 ref={headingRef} id="hero-title" className={styles.heroTitle} tabIndex={-1}>
            {m.home.title}
          </h1>
          <p className={styles.heroLead}>{m.home.lead}</p>
          <div className={styles.heroActions}>
            <ButtonLink to={PATHS.competitions}>{m.home.ctaCompetitions}</ButtonLink>
            <ButtonLink to={PATHS.fixtures} variant="secondary" className={styles.heroSecondary}>
              {m.home.ctaFixtures}
            </ButtonLink>
            <ButtonLink to={PATHS.methodology} variant="secondary" className={styles.heroSecondary}>
              {m.home.ctaMethodology}
            </ButtonLink>
          </div>
          <p className={styles.heroStatus}>
            <span className={styles.statusDot} aria-hidden="true" />
            {m.home.statusLine}
          </p>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="journey-title">
        <div className="container">
          <h2 id="journey-title">{m.home.journeyTitle}</h2>
          <p className={cx(styles.sectionLead, 'muted')}>{m.home.journeyLead}</p>
          <ol className={styles.journey}>
            {m.home.journey.map((step, index) => (
              <li key={step.title} className={styles.step}>
                <span className={styles.stepNumber} aria-hidden="true">
                  {index + 1}
                </span>
                <h3 className={styles.stepTitle}>{step.title}</h3>
                <p>{step.body}</p>
                <ButtonLink to={JOURNEY_PATHS[index] ?? PATHS.home} variant="ghost" className={styles.stepLink}>
                  {step.cta}
                </ButtonLink>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className={cx(styles.section, styles.alt)} aria-labelledby="principles-title">
        <div className="container">
          <h2 id="principles-title">{m.home.principlesTitle}</h2>
          <p className={cx(styles.sectionLead, 'muted')}>{m.home.principlesLead}</p>
          <ul className={styles.grid}>
            {m.home.principles.map((principle) => (
              <Card key={principle.title} as="li" title={principle.title}>
                <p>{principle.body}</p>
              </Card>
            ))}
          </ul>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="status-title">
        <div className="container">
          <h2 id="status-title">{m.home.statusTitle}</h2>
          <p className={cx(styles.sectionLead, 'muted')}>{m.home.statusLead}</p>
          <div className={styles.statusGrid}>
            <CapabilityList />
            <div className={styles.statusAside}>
              <h3>{m.productCodes.title}</h3>
              <ProductCodes />
              <DataServiceStatus />
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
