import { Link } from 'react-router';
import { CapabilityList } from '../components/ui/CapabilityList.tsx';
import { Card } from '../components/ui/Card.tsx';
import { Notice } from '../components/ui/Notice.tsx';
import { PageHeader } from '../components/ui/PageHeader.tsx';
import styles from './ContentPage.module.css';
import { cx } from '../lib/cx.ts';

const PIPELINE = [
  {
    title: 'Ingest and normalize',
    body: 'Provider data passes through an adapter into PREDIKTIA’s own model. Provider-specific semantics stay in the adapter; scores are stored as complete pairs and never half-updated.',
  },
  {
    title: 'Build point-in-time features',
    body: 'Inputs are reconstructed “as of” a cut-off before kickoff. Data whose availability cannot be proven for that moment is treated as unknown, not assumed.',
  },
  {
    title: 'Start from transparent baselines',
    body: 'Simple, well-understood references — such as an independent Poisson model of goals — set the bar any more complex model must clear.',
  },
  {
    title: 'Model full outcome distributions',
    body: 'Models describe the distribution of 90-minute goals, from which market-style quantities (for example over/under lines or both teams to score) are projected consistently instead of being modelled separately.',
  },
  {
    title: 'Evaluate out of sample',
    body: 'Performance is measured on matches the model never saw, with proper scoring rules, calibration slopes and checks of stability over time.',
  },
  {
    title: 'Reach an evidence-aware verdict',
    body: 'Diagnostics end in an explicit verdict. When the evidence is insufficient or inconclusive, the verdict is to abstain.',
  },
] as const;

const COMPARISON = [
  { aspect: 'Purpose', research: 'Learn whether a modelling approach works', certified: 'Inform a real decision about a future match' },
  { aspect: 'Data regime', research: 'Historical backtests, partly reconstructed after the fact', certified: 'Strictly point-in-time inputs, provably available before kickoff' },
  { aspect: 'Timing proof', research: 'Not required', certified: 'Independently authenticated issuance before kickoff' },
  { aspect: 'Validation', research: 'Diagnostics on past samples', certified: 'Prospective validation plus all evidence requirements met' },
  { aspect: 'Visibility', research: 'Internal; summarized qualitatively here', certified: 'Published only after explicit authorization' },
  { aspect: 'Status today', research: 'Active', certified: 'None exist — not authorized' },
] as const;

const LIMITATIONS = [
  'No model output has been certified, and none is authorized for publication.',
  'Historical backtests partly rely on data whose availability was reconstructed after the fact; they are labelled as retrospective and cannot prove what was knowable at the time.',
  'There is no authenticated, timestamped source of bookmaker odds, so no market comparison or expected-value figure can be trusted — and none is shown.',
  'Calibration diagnostics describe past samples. They can show a model is miscalibrated, but cannot certify that future forecasts will be calibrated.',
  'Matches decided after extra time or penalties are excluded from 90-minute goal research, so those outcomes are not modelled.',
  'Some inputs, such as expected-goals statistics, have limited historical coverage and are treated as optional.',
] as const;

export function ResearchPage() {
  return (
    <>
      <PageHeader
        eyebrow="Research"
        title="Quantitative research approach"
        lead={
          <p>
            How PREDIKTIA studies football outcomes — and why none of that research is presented as a prediction.
          </p>
        }
      />

      <div className={cx('container', styles.body)}>
        <Notice tone="warning" title="Research is not prediction">
          <p>
            Research models are experiments evaluated on historical data. Their diagnostics help decide whether an
            approach is worth pursuing; they do not certify any forecast. This page deliberately contains no
            probabilities, accuracy figures or performance statistics.
          </p>
        </Notice>

        <section aria-labelledby="pipeline-title" className={styles.section}>
          <h2 id="pipeline-title">How the research works</h2>
          <ol className={styles.steps}>
            {PIPELINE.map((step) => (
              <li key={step.title} className={styles.step}>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section aria-labelledby="comparison-title" className={styles.section}>
          <h2 id="comparison-title">Research model vs. certified prediction</h2>
          <p className="muted prose">
            The two are different products with different standards of proof. Only the first exists today.
          </p>
          {/* Scrollable on narrow screens: focusable so keyboard users can scroll it. */}
          <div className={styles.tableWrap} role="region" aria-labelledby="comparison-title" tabIndex={0}>
            <table className={styles.table}>
              <caption className="visually-hidden">Comparison of research models and certified predictions</caption>
              <thead>
                <tr>
                  <th scope="col">Aspect</th>
                  <th scope="col">Research model</th>
                  <th scope="col">Certified prediction</th>
                </tr>
              </thead>
              <tbody>
                {COMPARISON.map((row) => (
                  <tr key={row.aspect}>
                    <th scope="row">{row.aspect}</th>
                    <td>{row.research}</td>
                    <td>{row.certified}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-labelledby="limitations-title" className={styles.section}>
          <h2 id="limitations-title">Current limitations</h2>
          <ul className={cx(styles.checklist, 'prose')}>
            {LIMITATIONS.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="status-title" className={styles.section}>
          <h2 id="status-title">Platform status</h2>
          <CapabilityList />
        </section>

        <Card variant="muted" title="Want the details?" headingLevel={2}>
          <p>
            The <Link to="/methodology">methodology page</Link> covers data provenance, validation, calibration limits
            and the evidence required before anything could be published.
          </p>
        </Card>
      </div>
    </>
  );
}
