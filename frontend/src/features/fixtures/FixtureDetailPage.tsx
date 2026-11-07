import type { ReactNode } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useApi } from '../../api/apiContext.ts';
import { isNotFound } from '../../api/errors.ts';
import type { Competition, Fixture } from '../../api/schemas.ts';
import { competitionPath, PATHS } from '../../components/layout/navigation.ts';
import { ApiFailure } from '../../components/ui/ApiFailure.tsx';
import { ButtonLink } from '../../components/ui/Button.tsx';
import type { Crumb } from '../../components/ui/Breadcrumbs.tsx';
import { IdProblem } from '../../components/ui/IdProblem.tsx';
import { Notice } from '../../components/ui/Notice.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, LoadingState } from '../../components/ui/States.tsx';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useResource } from '../../hooks/useResource.ts';
import { useI18n } from '../../i18n/i18n.ts';
import { parseId } from '../../lib/ids.ts';
import { ResearchContext } from './AbstainPanel.tsx';
import { fixturesHref } from './fixtureFilters.ts';
import { fixturePhase, fixtureStatusLabel, hasFinalScore, phaseTone, scoreLines } from './fixtureStatus.ts';
import styles from './Fixtures.module.css';
import { cx } from '../../lib/cx.ts';

function useBaseCrumbs(): Crumb[] {
  const { messages: m } = useI18n();
  return [
    { label: m.nav.home, to: PATHS.home },
    { label: m.nav.fixtures, to: PATHS.fixtures },
  ];
}

/** Link back to the list the user came from (its filters travel in navigation state), else the fixture's competition. */
function useBackHref(fallback: string): string {
  const state: unknown = useLocation().state;
  if (typeof state === 'object' && state !== null && 'from' in state && typeof state.from === 'string' && /^(\?.*)?$/.test(state.from)) {
    return `${PATHS.fixtures}${state.from}`;
  }
  return fallback;
}

function BackLink({ to }: { to: string }) {
  const { messages: m } = useI18n();
  return (
    <p className={styles.back}>
      <Link to={to}>
        <span aria-hidden="true">←</span>
        {m.fixture.back}
      </Link>
    </p>
  );
}

function NotFoundFixture() {
  const { messages: m } = useI18n();
  const crumbs = useBaseCrumbs();
  return (
    <>
      <PageHeader title={m.fixture.notFoundTitle} eyebrow={m.fixture.eyebrow} breadcrumbs={[...crumbs, { label: m.fixture.notFoundTitle }]} />
      <div className={cx('container', styles.page)}>
        <EmptyState title={m.fixture.notFoundTitle} headingLevel={2} action={<ButtonLink to={PATHS.fixtures}>{m.fixture.browse}</ButtonLink>}>
          <p>{m.fixture.notFound}</p>
        </EmptyState>
      </div>
    </>
  );
}

export function FixtureDetailPage() {
  const { messages: m } = useI18n();
  const crumbs = useBaseCrumbs();
  const { fixtureId: raw } = useParams();
  const id = parseId(raw);
  if (id.kind !== 'valid') {
    return <IdProblem id={id} eyebrow={m.fixture.eyebrow} breadcrumbs={crumbs} backTo={PATHS.fixtures} backLabel={m.fixture.browse} />;
  }
  return <FixtureDetail key={id.value} fixtureId={id.value} />;
}

function FixtureDetail({ fixtureId }: { fixtureId: number }) {
  const { messages: m } = useI18n();
  const api = useApi();
  const crumbs = useBaseCrumbs();
  const back = useBackHref(PATHS.fixtures);
  const fixture = useResource(`fixture:${String(fixtureId)}`, (signal) => api.getFixture(fixtureId, signal));

  switch (fixture.state.status) {
    case 'idle':
    case 'loading':
      return (
        <>
          <PageHeader title={m.fixture.loadingTitle} eyebrow={m.fixture.eyebrow} breadcrumbs={[...crumbs, { label: m.fixture.loadingTitle }]} />
          <div className={cx('container', styles.page)}>
            <LoadingState label={m.fixture.loading} rows={2} />
          </div>
        </>
      );
    case 'error':
      if (isNotFound(fixture.state.error)) return <NotFoundFixture />;
      return (
        <>
          <PageHeader title={m.fixture.loadingTitle} eyebrow={m.fixture.eyebrow} breadcrumbs={[...crumbs, { label: m.fixture.loadingTitle }]} />
          <div className={cx('container', styles.page)}>
            <BackLink to={back} />
            <ApiFailure error={fixture.state.error} subject={m.subjects.fixture} onRetry={fixture.reload} headingLevel={2} />
          </div>
        </>
      );
    case 'success':
      return <FixtureView fixture={fixture.state.data} />;
  }
}

function FixtureView({ fixture }: { fixture: Fixture }) {
  const { messages: m, format } = useI18n();
  const api = useApi();
  const crumbs = useBaseCrumbs();
  // Only for the competition's name; the page never depends on it.
  const competitions = useResource('competitions', (signal) => api.listCompetitions(signal));
  const competition: Competition | undefined =
    competitions.state.status === 'success' ? competitions.state.data.find((item) => item.id === fixture.competition_id) : undefined;
  const competitionName = competition?.name ?? m.fixture.competitionFallback(fixture.competition_id);
  const back = useBackHref(fixturesHref({ competitionId: fixture.competition_id, season: fixture.season }));

  const phase = fixturePhase(fixture.status_short);
  const lines = scoreLines(fixture);
  const final = hasFinalScore(fixture);
  const title = m.fixture.title(fixture.home_team.name, fixture.away_team.name);

  const details: { term: string; value: ReactNode }[] = [
    {
      term: m.fixture.kickoffChile,
      value: (
        <>
          <time dateTime={fixture.kickoff_at}>{format.kickoff(fixture.kickoff_at)}</time> ({format.offset(fixture.kickoff_at)})
        </>
      ),
    },
    { term: m.fixture.kickoffUtc, value: format.utc(fixture.kickoff_at) },
    { term: m.fixture.competition, value: <Link to={competitionPath(fixture.competition_id)}>{competitionName}</Link> },
    { term: m.fixture.season, value: String(fixture.season) },
    { term: m.fixture.round, value: fixture.round },
    { term: m.fixture.venue, value: [fixture.venue_name, fixture.venue_city].filter(Boolean).join(', ') || null },
    { term: m.fixture.referee, value: fixture.referee },
    { term: m.fixture.statusCode, value: <code>{fixture.status_short}</code> },
  ];

  return (
    <>
      <PageHeader
        breadcrumbs={[...crumbs, { label: title }]}
        eyebrow={m.fixture.eyebrow}
        title={title}
        lead={
          <p className={styles.headerStatus}>
            <StatusBadge tone={phaseTone(phase)}>{fixtureStatusLabel(fixture, m)}</StatusBadge>
            <span>
              {m.fixture.kickoffLead} <time dateTime={fixture.kickoff_at}>{format.kickoff(fixture.kickoff_at)}</time> ({format.offset(fixture.kickoff_at)})
            </span>
          </p>
        }
      />
      <div className={cx('container', styles.page)}>
        <BackLink to={back} />
        <Notice>
          <p>{m.fixture.syncNote}</p>
        </Notice>
        <div className={styles.detailGrid}>
          <section className={styles.panel} aria-labelledby="score-heading">
            <h2 id="score-heading" className={styles.panelTitle}>
              {m.fixture.scoreTitle}
            </h2>
            {lines.length > 0 ? (
              <>
                <table className={styles.scoreTable}>
                  <caption className="visually-hidden">{m.fixture.scoreCaption(title)}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{m.fixture.period}</th>
                      <th scope="col">{fixture.home_team.name}</th>
                      <th scope="col">{fixture.away_team.name}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line) => (
                      <tr key={line.kind}>
                        <th scope="row">{m.scoreKinds[line.kind]}</th>
                        <td>{line.home}</td>
                        <td>{line.away}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className={styles.footnote}>{final ? m.fixture.finalNote : m.fixture.notFinalNote}</p>
              </>
            ) : (
              <p className="muted">{m.fixture.noScore}</p>
            )}
            {phase === 'in_play' ? <p className={styles.footnote}>{m.fixture.inPlayNote(fixture.elapsed)}</p> : null}
          </section>

          <section className={styles.panel} aria-labelledby="details-heading">
            <h2 id="details-heading" className={styles.panelTitle}>
              {m.fixture.detailsTitle}
            </h2>
            <dl className={styles.details}>
              {details.map((item) => (
                <div key={item.term}>
                  <dt>{item.term}</dt>
                  <dd>{item.value ?? <span className="muted">{m.fixture.notRecorded}</span>}</dd>
                </div>
              ))}
            </dl>
          </section>
        </div>

        <ResearchContext fixture={fixture} />

        <nav className={styles.more} aria-labelledby="more-heading">
          <h2 id="more-heading" className={styles.panelTitle}>
            {m.fixture.moreTitle}
          </h2>
          <ul>
            <li>
              <Link to={fixturesHref({ competitionId: fixture.competition_id, season: fixture.season })}>
                {m.fixture.moreCompetition(competitionName, fixture.season)}
              </Link>
            </li>
            {[fixture.home_team, fixture.away_team].map((team) => (
              <li key={team.id}>
                <Link to={fixturesHref({ competitionId: fixture.competition_id, season: fixture.season, teamId: team.id })}>
                  {m.fixture.moreTeam(team.name)}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </>
  );
}
