import { Link } from 'react-router';
import { Notice } from '../components/ui/Notice.tsx';
import { PageHeader } from '../components/ui/PageHeader.tsx';
import { StatusBadge } from '../components/ui/StatusBadge.tsx';
import { EVIDENCE_REQUIREMENTS } from '../content/productStatus.ts';
import styles from './ContentPage.module.css';
import { cx } from '../lib/cx.ts';

const SECTIONS = [
  { id: 'provenance', title: 'Historical data and provenance' },
  { id: 'point-in-time', title: 'Point-in-time discipline' },
  { id: 'validation', title: 'Model validation' },
  { id: 'calibration', title: 'Calibration limitations' },
  { id: 'research-only', title: 'Research-only status' },
  { id: 'evidence', title: 'Evidence requirements' },
  { id: 'abstain', title: 'ABSTAIN-first behavior' },
] as const;

const ABSTAIN_GATES = [
  { question: 'Are all inputs certified as known before kickoff?', outcome: 'If not proven: ABSTAIN.' },
  { question: 'Has the model been validated out of sample, prospectively?', outcome: 'If not: ABSTAIN.' },
  { question: 'Is calibration acceptable on recent, relevant data?', outcome: 'If unknown or poor: ABSTAIN.' },
  { question: 'Is the issuance time independently authenticated?', outcome: 'If not: ABSTAIN.' },
  { question: 'Is publication explicitly authorized?', outcome: 'If not: ABSTAIN.' },
] as const;

export function MethodologyPage() {
  return (
    <>
      <PageHeader
        eyebrow="Methodology"
        title="Methodology and evidence"
        lead={
          <p>
            The standards PREDIKTIA holds itself to, what has and has not been established, and why the default answer
            is to abstain.
          </p>
        }
      />

      <div className={cx('container', styles.body)}>
        <div className={styles.layout}>
          <nav className={styles.toc} aria-labelledby="toc-title">
            <h2 id="toc-title">On this page</h2>
            <ol>
              {SECTIONS.map((section) => (
                <li key={section.id}>
                  <a href={`#${section.id}`}>{section.title}</a>
                </li>
              ))}
            </ol>
          </nav>

          <article className={styles.article}>
            <section id="provenance" aria-labelledby="provenance-title">
              <h2 id="provenance-title">Historical data and provenance</h2>
              <p>
                Competitions, teams, fixtures and results are obtained from external football data providers. Each
                provider is wrapped by an adapter that translates its format into PREDIKTIA’s own data model, so that
                provider-specific semantics never leak into research code. Data flows in one direction: provider,
                adapter, internal model, service, repository, database.
              </p>
              <ul>
                <li>
                  Scores are stored as complete home/away pairs. A partial or empty provider response never overwrites
                  a known score.
                </li>
                <li>
                  The 90-minute score is kept separately from the final score, because markets settled at full time
                  must not include extra time or penalties.
                </li>
                <li>Observations are recorded with when they were seen, so the history of a fixture can be audited.</li>
              </ul>
            </section>

            <section id="point-in-time" aria-labelledby="point-in-time-title">
              <h2 id="point-in-time-title">Point-in-time discipline</h2>
              <p>
                A forecast is only honest if it uses information that existed when it was made. Research features are
                therefore reconstructed “as of” a cut-off before kickoff. Anything whose availability at that moment
                cannot be shown is treated as unknown.
              </p>
              <p>
                Part of the historical record was backfilled after the fact. Research that uses it is labelled as a
                retrospective backtest: useful for learning, but not proof of what a model could have known in real
                time.
              </p>
            </section>

            <section id="validation" aria-labelledby="validation-title">
              <h2 id="validation-title">Model validation</h2>
              <p>
                Models are compared against transparent baselines and evaluated on matches they were not fitted on.
                Evaluation uses proper scoring rules, which reward honest probabilities rather than bold ones, together
                with checks of how stable results are over time.
              </p>
              <p>
                Passing these diagnostics makes an approach a candidate for further work. It does not make any of its
                outputs a prediction.
              </p>
            </section>

            <section id="calibration" aria-labelledby="calibration-title">
              <h2 id="calibration-title">Calibration limitations</h2>
              <p>
                Calibration asks whether events assigned a given probability actually happen about that often. Diagnostics such
                as calibration slopes describe how a model behaved on a past sample. They have important limits:
              </p>
              <ul>
                <li>They are estimates with uncertainty, and small samples can hide or exaggerate miscalibration.</li>
                <li>Calibration can drift as competitions, teams and data sources change.</li>
                <li>Good historical calibration does not certify that future forecasts will be calibrated.</li>
              </ul>
            </section>

            <section id="research-only" aria-labelledby="research-only-title">
              <h2 id="research-only-title">Research-only status</h2>
              <Notice tone="warning" title="No certified predictions">
                <p>
                  PREDIKTIA currently has no authorization to show certified predictions. This interface shows no
                  probabilities, odds, expected values, stakes, betting recommendations or model accuracy figures, and
                  research diagnostics are never presented as forecasts.
                </p>
              </Notice>
            </section>

            <section id="evidence" aria-labelledby="evidence-title">
              <h2 id="evidence-title">Evidence requirements</h2>
              <p>
                Before any prediction could be published, each of the following must exist. None is in place today.
              </p>
              <ul className={styles.requirements}>
                {EVIDENCE_REQUIREMENTS.map((item) => (
                  <li key={item.id} className={styles.requirement}>
                    <h3>{item.requirement}</h3>
                    <StatusBadge tone="abstain">{item.status}</StatusBadge>
                    <p>{item.why}</p>
                  </li>
                ))}
              </ul>
            </section>

            <section id="abstain" aria-labelledby="abstain-title">
              <h2 id="abstain-title">ABSTAIN-first behavior</h2>
              <p>
                Abstaining is the default outcome, not a failure mode. A prediction would only be shown if every gate
                below were passed; failing any single gate means ABSTAIN.
              </p>
              <ol className={styles.flow}>
                {ABSTAIN_GATES.map((gate) => (
                  <li key={gate.question}>
                    {gate.question}
                    <span className={styles.flowOutcome}>{gate.outcome}</span>
                  </li>
                ))}
              </ol>
              <p>
                Today every fixture resolves to ABSTAIN. You can see this on any fixture in the{' '}
                <Link to="/fixtures">fixture browser</Link>.
              </p>
            </section>
          </article>
        </div>
      </div>
    </>
  );
}
