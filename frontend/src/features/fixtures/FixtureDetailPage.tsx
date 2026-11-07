import { Link, useParams } from 'react-router';
import { useApi } from '../../api/apiContext.ts';
import { isNotFound } from '../../api/errors.ts';
import type { Fixture } from '../../api/schemas.ts';
import { ApiFailure } from '../../components/ui/ApiFailure.tsx';
import { ButtonLink } from '../../components/ui/Button.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, LoadingState } from '../../components/ui/States.tsx';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useResource } from '../../hooks/useResource.ts';
import { formatKickoff, parsePositiveInt } from '../../lib/format.ts';
import { AbstainPanel } from './AbstainPanel.tsx';
import { fixturePhase, fixtureStatusLabel, hasFinalScore, phaseTone, scoreLines } from './fixtureStatus.ts';
import styles from './Fixtures.module.css';
import { cx } from '../../lib/cx.ts';

function BackLink() {
  return (
    <p className={styles.back}>
      <Link to="/fixtures">← Back to fixtures</Link>
    </p>
  );
}

function NotFoundFixture() {
  return (
    <>
      <PageHeader title="Fixture not found" eyebrow="Fixture" />
      <div className={cx('container', styles.page)}>
        <EmptyState title="This fixture does not exist" headingLevel={2} action={<ButtonLink to="/fixtures">Browse fixtures</ButtonLink>}>
          <p>There is no fixture with this identifier in the PREDIKTIA data store.</p>
        </EmptyState>
      </div>
    </>
  );
}

export function FixtureDetailPage() {
  const { fixtureId: rawId } = useParams();
  const fixtureId = parsePositiveInt(rawId);
  if (fixtureId === undefined) return <NotFoundFixture />;
  return <FixtureDetail key={fixtureId} fixtureId={fixtureId} />;
}

function FixtureDetail({ fixtureId }: { fixtureId: number }) {
  const api = useApi();
  const fixture = useResource(`fixture:${fixtureId}`, (signal) => api.getFixture(fixtureId, signal));

  switch (fixture.state.status) {
    case 'idle':
    case 'loading':
      return (
        <>
          <PageHeader title="Fixture" eyebrow="Fixture" />
          <div className={cx('container', styles.page)}>
            <LoadingState label="Loading fixture…" rows={2} />
          </div>
        </>
      );
    case 'error':
      if (isNotFound(fixture.state.error)) return <NotFoundFixture />;
      return (
        <>
          <PageHeader title="Fixture" eyebrow="Fixture" />
          <div className={cx('container', styles.page)}>
            <BackLink />
            <ApiFailure error={fixture.state.error} subject="this fixture" onRetry={fixture.reload} headingLevel={2} />
          </div>
        </>
      );
    case 'success':
      return <FixtureView fixture={fixture.state.data} />;
  }
}

function FixtureView({ fixture }: { fixture: Fixture }) {
  const phase = fixturePhase(fixture.status_short);
  const lines = scoreLines(fixture);
  const final = hasFinalScore(fixture);
  const title = `${fixture.home_team.name} vs ${fixture.away_team.name}`;

  const details: { term: string; value: string | null }[] = [
    { term: 'Kickoff', value: formatKickoff(fixture.kickoff_at) },
    { term: 'Season', value: String(fixture.season) },
    { term: 'Round', value: fixture.round },
    { term: 'Venue', value: [fixture.venue_name, fixture.venue_city].filter(Boolean).join(', ') || null },
    { term: 'Referee', value: fixture.referee },
    { term: 'Status code', value: fixture.status_short },
  ];

  return (
    <>
      <PageHeader
        eyebrow="Fixture"
        title={title}
        lead={
          <p className={styles.headerStatus}>
            <StatusBadge tone={phaseTone(phase)}>{fixtureStatusLabel(fixture)}</StatusBadge>
            <span>
              Kickoff <time dateTime={fixture.kickoff_at}>{formatKickoff(fixture.kickoff_at)}</time>
            </span>
          </p>
        }
      />
      <div className={cx('container', styles.page)}>
        <BackLink />
        <div className={styles.detailGrid}>
          <section className={styles.panel} aria-labelledby="score-heading">
            <h2 id="score-heading" className={styles.panelTitle}>
              Score
            </h2>
            {lines.length > 0 ? (
              <>
                <table className={styles.scoreTable}>
                  <caption className="visually-hidden">Recorded scores for {title}</caption>
                  <thead>
                    <tr>
                      <th scope="col">Period</th>
                      <th scope="col">{fixture.home_team.name}</th>
                      <th scope="col">{fixture.away_team.name}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line) => (
                      <tr key={line.label}>
                        <th scope="row">{line.label}</th>
                        <td>{line.home}</td>
                        <td>{line.away}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className={styles.footnote}>
                  {final
                    ? 'The final score includes extra time when played; penalty shoot-outs are listed separately. '
                    : 'This fixture had not finished at the last synchronization, so the score shown is not a final result. '}
                  Scores are shown as stored at the last synchronization.
                </p>
              </>
            ) : (
              <p className="muted">No score is recorded for this fixture.</p>
            )}
            {phase === 'in_play' ? (
              <p className={styles.footnote}>
                This fixture was in progress at the last synchronization. This is not a live score.
              </p>
            ) : null}
          </section>

          <section className={styles.panel} aria-labelledby="details-heading">
            <h2 id="details-heading" className={styles.panelTitle}>
              Match details
            </h2>
            <dl className={styles.details}>
              {details.map((item) => (
                <div key={item.term}>
                  <dt>{item.term}</dt>
                  <dd>{item.value ?? <span className="muted">Not recorded</span>}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>
        <AbstainPanel />
      </div>
    </>
  );
}
