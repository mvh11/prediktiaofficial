import { ButtonLink } from '../components/ui/Button.tsx';
import { CapabilityList } from '../components/ui/CapabilityList.tsx';
import { Card } from '../components/ui/Card.tsx';
import { SITE_NAME, useDocumentTitle } from '../hooks/useDocumentTitle.ts';
import { useNavigationFocus } from '../hooks/useNavigationFocus.ts';
import styles from './HomePage.module.css';
import { cx } from '../lib/cx.ts';

const PRINCIPLES = [
  {
    title: 'Data with provenance',
    body: 'Fixtures and results are ingested from external providers through isolated adapters into PREDIKTIA’s own data model, with each observation tracked so its origin and timing can be audited.',
  },
  {
    title: 'Point-in-time research',
    body: 'Models may only use information that was available before kickoff. Features are built “as of” a moment in time to rule out look-ahead bias.',
  },
  {
    title: 'Calibration before claims',
    body: 'A probability is only useful if it is calibrated. Models are evaluated out of sample with proper scoring rules and calibration diagnostics before anything is considered for publication.',
  },
  {
    title: 'ABSTAIN by default',
    body: 'When evidence is missing or insufficient, the correct output is no output. PREDIKTIA abstains rather than guess — today, that means for every fixture.',
  },
] as const;

const EXPLORE = [
  {
    to: '/fixtures',
    title: 'Browse fixtures',
    body: 'Competitions and matches from the PREDIKTIA data store, with honest states when the data service is not available.',
    cta: 'Open fixtures',
  },
  {
    to: '/research',
    title: 'Research approach',
    body: 'How models are built and evaluated, what the current limitations are, and why research outputs are not predictions.',
    cta: 'Read about the research',
  },
  {
    to: '/methodology',
    title: 'Methodology & evidence',
    body: 'Data provenance, validation, calibration limits and the evidence required before any prediction could be published.',
    cta: 'Review the methodology',
  },
] as const;

export function HomePage() {
  useDocumentTitle(SITE_NAME);
  const headingRef = useNavigationFocus<HTMLHeadingElement>();
  return (
    <>
      <section className={styles.hero} aria-labelledby="hero-title">
        <div className={cx('container', styles.heroInner)}>
          <p className={styles.kicker}>Quantitative football research</p>
          <h1 ref={headingRef} id="hero-title" className={styles.heroTitle} tabIndex={-1}>
            Football probabilities, built on evidence — or not at all.
          </h1>
          <p className={styles.heroLead}>
            PREDIKTIA is building a research platform for football forecasting: point-in-time data, transparent
            probabilistic models and rigorous calibration checks. Until the evidence is there, it publishes no
            predictions.
          </p>
          <div className={styles.heroActions}>
            <ButtonLink to="/fixtures">Browse fixtures</ButtonLink>
            <ButtonLink to="/methodology" variant="secondary" className={styles.heroSecondary}>
              Read the methodology
            </ButtonLink>
          </div>
          <p className={styles.heroStatus}>
            <span className={styles.statusDot} aria-hidden="true" />
            Research preview · No certified predictions · No betting recommendations
          </p>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="principles-title">
        <div className="container">
          <h2 id="principles-title">How PREDIKTIA works</h2>
          <p className={cx(styles.sectionLead, 'muted')}>
            Four principles shape every part of the platform, from data ingestion to what this interface is allowed to
            show.
          </p>
          <ul className={styles.grid} aria-label="Principles">
            {PRINCIPLES.map((principle) => (
              <Card key={principle.title} as="li" title={principle.title}>
                <p>{principle.body}</p>
              </Card>
            ))}
          </ul>
        </div>
      </section>

      <section className={cx(styles.section, styles.alt)} aria-labelledby="status-title">
        <div className="container">
          <h2 id="status-title">Where the platform stands today</h2>
          <p className={cx(styles.sectionLead, 'muted')}>
            A plain statement of what exists and what does not. This list is maintained alongside the code, not by
            marketing.
          </p>
          <CapabilityList />
        </div>
      </section>

      <section className={styles.section} aria-labelledby="explore-title">
        <div className="container">
          <h2 id="explore-title">Explore</h2>
          <ul className={styles.grid} aria-label="Explore PREDIKTIA">
            {EXPLORE.map((item) => (
              <Card
                key={item.to}
                as="li"
                title={item.title}
                footer={
                  <ButtonLink to={item.to} variant="ghost">
                    {item.cta}
                  </ButtonLink>
                }
              >
                <p>{item.body}</p>
              </Card>
            ))}
          </ul>
        </div>
      </section>
    </>
  );
}
