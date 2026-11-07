import { useId, useState, type SubmitEvent } from 'react';
import { Link, useParams, useSearchParams } from 'react-router';
import { useApi } from '../../api/apiContext.ts';
import { isNotFound } from '../../api/errors.ts';
import { fetchFixturePage } from '../../api/prediktiaApi.ts';
import type { Competition, Team } from '../../api/schemas.ts';
import { PATHS } from '../../components/layout/navigation.ts';
import { ApiFailure } from '../../components/ui/ApiFailure.tsx';
import { Button, ButtonLink } from '../../components/ui/Button.tsx';
import { InputField } from '../../components/ui/Field.tsx';
import { IdProblem } from '../../components/ui/IdProblem.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, LoadingState } from '../../components/ui/States.tsx';
import { StatusBadge } from '../../components/ui/StatusBadge.tsx';
import { useResource } from '../../hooks/useResource.ts';
import { utcDay } from '../../i18n/format.ts';
import { useI18n } from '../../i18n/i18n.ts';
import { normalizeForSearch } from '../../lib/format.ts';
import { parseId } from '../../lib/ids.ts';
import { DataServiceStatus } from '../system/DataServiceStatus.tsx';
import { FixtureList } from '../fixtures/FixtureList.tsx';
import { fixturesHref, MAX_SEASON, MIN_SEASON, parseSeason } from '../fixtures/fixtureFilters.ts';
import { competitionType } from './competitionText.ts';
import styles from './Competitions.module.css';
import { cx } from '../../lib/cx.ts';

const UPCOMING_COUNT = 6;

export function CompetitionDetailPage() {
  const { messages: m } = useI18n();
  const { competitionId: raw } = useParams();
  const id = parseId(raw);
  const crumbs = [
    { label: m.nav.home, to: PATHS.home },
    { label: m.nav.competitions, to: PATHS.competitions },
  ];
  if (id.kind !== 'valid') {
    return <IdProblem id={id} eyebrow={m.competition.eyebrow} breadcrumbs={crumbs} backTo={PATHS.competitions} backLabel={m.competition.backToList} />;
  }
  return <CompetitionLoader key={id.value} competitionId={id.value} />;
}

/** There is no single-competition endpoint: the competition is looked up in the verified list. */
function CompetitionLoader({ competitionId }: { competitionId: number }) {
  const { messages: m } = useI18n();
  const api = useApi();
  const competitions = useResource('competitions', (signal) => api.listCompetitions(signal));
  const crumbs = [
    { label: m.nav.home, to: PATHS.home },
    { label: m.nav.competitions, to: PATHS.competitions },
  ];

  switch (competitions.state.status) {
    case 'idle':
    case 'loading':
      return (
        <>
          <PageHeader title={m.competition.loadingTitle} eyebrow={m.competition.eyebrow} breadcrumbs={[...crumbs, { label: m.competition.loadingTitle }]} />
          <div className={cx('container', styles.page)}>
            <LoadingState label={m.competition.loading} rows={2} />
          </div>
        </>
      );
    case 'error':
      return (
        <>
          <PageHeader title={m.competition.loadingTitle} eyebrow={m.competition.eyebrow} breadcrumbs={[...crumbs, { label: m.competition.loadingTitle }]} />
          <div className={cx('container', styles.page)}>
            <ApiFailure error={competitions.state.error} subject={m.subjects.competition} onRetry={competitions.reload} headingLevel={2} />
          </div>
        </>
      );
    case 'success': {
      const competition = competitions.state.data.find((item) => item.id === competitionId);
      if (competition === undefined) {
        return (
          <>
            <PageHeader title={m.competition.notFoundTitle} eyebrow={m.competition.eyebrow} breadcrumbs={[...crumbs, { label: m.competition.notFoundTitle }]} />
            <div className={cx('container', styles.page)}>
              <EmptyState title={m.competition.notFoundTitle} headingLevel={2} action={<ButtonLink to={PATHS.competitions}>{m.competition.backToList}</ButtonLink>}>
                <p>{m.competition.notFound}</p>
              </EmptyState>
            </div>
          </>
        );
      }
      return <CompetitionView competition={competition} />;
    }
  }
}

function CompetitionView({ competition }: { competition: Competition }) {
  const { messages: m } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedSeason = parseSeason(searchParams.get('temporada'));
  const season = requestedSeason ?? competition.current_season ?? undefined;
  const type = competitionType(competition.type, m);
  const leadParts = [type, competition.country].filter((part): part is string => part !== null);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: m.nav.home, to: PATHS.home },
          { label: m.nav.competitions, to: PATHS.competitions },
          { label: competition.name },
        ]}
        eyebrow={m.competition.eyebrow}
        title={competition.name}
        lead={leadParts.length > 0 ? <p>{leadParts.join(' · ')}</p> : undefined}
        actions={<DataServiceStatus />}
      />
      <div className={cx('container', styles.page)}>
        <div className={styles.overview}>
          <section className={styles.panel} aria-labelledby="facts-title">
            <h2 id="facts-title" className={styles.panelTitle}>
              {m.competition.facts}
            </h2>
            <dl className={styles.facts}>
              <div>
                <dt>{m.competition.country}</dt>
                <dd>{competition.country ?? <span className="muted">{m.competition.notRecorded}</span>}</dd>
              </div>
              <div>
                <dt>{m.competition.type}</dt>
                <dd>{type ?? <span className="muted">{m.competition.notRecorded}</span>}</dd>
              </div>
              <div>
                <dt>{m.competition.currentSeason}</dt>
                <dd>{competition.current_season ?? <span className="muted">{m.competition.noCurrentSeason}</span>}</dd>
              </div>
            </dl>
          </section>

          <section className={styles.panel} aria-labelledby="season-title">
            <h2 id="season-title" className={styles.panelTitle}>
              {m.competition.seasonPanelTitle}
            </h2>
            <SeasonForm
              key={String(season)}
              season={season}
              current={competition.current_season}
              onSeason={(value) => {
                const next = new URLSearchParams(searchParams);
                if (value === undefined || value === competition.current_season) next.delete('temporada');
                else next.set('temporada', String(value));
                setSearchParams(next);
              }}
            />
            <p className={styles.panelActions}>
              <ButtonLink to={fixturesHref({ competitionId: competition.id, season })}>
                {season === undefined ? m.competition.viewAllFixtures : m.competition.viewFixtures(season)}
              </ButtonLink>
            </p>
          </section>
        </div>

        <UpcomingFixtures competition={competition} season={season} />
        <TeamsSection key={String(season)} competition={competition} season={season} />
      </div>
    </>
  );
}

function SeasonForm({ season, current, onSeason }: { season: number | undefined; current: number | null; onSeason: (value: number | undefined) => void }) {
  const { messages: m } = useI18n();
  const [error, setError] = useState<string | undefined>(undefined);
  const onSubmit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get('temporada');
    const text = typeof value === 'string' ? value.trim() : '';
    const parsed = parseSeason(text);
    if (text !== '' && parsed === undefined) {
      setError(m.competition.seasonInvalid);
      return;
    }
    onSeason(parsed);
  };
  return (
    <form className={styles.seasonForm} onSubmit={onSubmit} noValidate>
      <InputField
        label={m.competition.seasonFormLabel}
        name="temporada"
        type="number"
        inputMode="numeric"
        min={MIN_SEASON}
        max={MAX_SEASON}
        step={1}
        defaultValue={season ?? ''}
        hint={m.competition.seasonFormHint(current)}
        error={error}
      />
      <Button type="submit" variant="secondary">
        {m.competition.seasonSubmit}
      </Button>
    </form>
  );
}

function UpcomingFixtures({ competition, season }: { competition: Competition; season: number | undefined }) {
  const { messages: m } = useI18n();
  const api = useApi();
  const today = utcDay();
  const query = { competitionId: competition.id, season, dateFrom: today, finished: false };
  const upcoming = useResource(`upcoming:${JSON.stringify(query)}`, (signal) => fetchFixturePage(api, query, 1, UPCOMING_COUNT, signal));

  let content;
  switch (upcoming.state.status) {
    case 'idle':
    case 'loading':
      content = <LoadingState label={m.competition.upcomingLoading} rows={2} />;
      break;
    case 'error':
      content = <ApiFailure error={upcoming.state.error} subject={m.subjects.upcoming} onRetry={upcoming.reload} />;
      break;
    case 'success':
      content =
        upcoming.state.data.fixtures.length === 0 ? (
          <EmptyState title={m.competition.upcomingEmptyTitle}>
            <p>{m.competition.upcomingEmpty}</p>
          </EmptyState>
        ) : (
          <>
            <FixtureList fixtures={upcoming.state.data.fixtures} returnSearch="" />
            <p>
              <Link to={fixturesHref({ competitionId: competition.id, season, dateFrom: today, finished: 'pending' })}>{m.competition.upcomingAll}</Link>
            </p>
          </>
        );
      break;
  }

  return (
    <section className={styles.section} aria-labelledby="upcoming-title">
      <h2 id="upcoming-title">{m.competition.upcomingTitle}</h2>
      <p className="muted">{m.competition.upcomingLead}</p>
      {content}
    </section>
  );
}

function TeamsSection({ competition, season }: { competition: Competition; season: number | undefined }) {
  const { messages: m } = useI18n();
  const api = useApi();
  const teams = useResource(`teams:${String(competition.id)}:${String(season ?? 'current')}`, (signal) =>
    api.listCompetitionTeams(competition.id, season, signal),
  );

  let content;
  switch (teams.state.status) {
    case 'idle':
    case 'loading':
      content = <LoadingState label={m.competition.teamsLoading} />;
      break;
    case 'error':
      content = isNotFound(teams.state.error) ? (
        <EmptyState title={m.competition.teamsSeasonMissingTitle}>
          <p>{m.competition.teamsSeasonMissing(season ?? null)}</p>
        </EmptyState>
      ) : (
        <ApiFailure error={teams.state.error} subject={m.subjects.teams} onRetry={teams.reload} />
      );
      break;
    case 'success':
      content =
        teams.state.data.length === 0 ? (
          <EmptyState title={m.competition.teamsEmptyTitle}>
            <p>{m.competition.teamsEmpty}</p>
          </EmptyState>
        ) : (
          <TeamDirectory teams={teams.state.data} competition={competition} season={season} />
        );
      break;
  }

  return (
    <section className={styles.section} aria-labelledby="teams-title">
      <h2 id="teams-title">{season === undefined ? m.competition.teamsTitleCurrent : m.competition.teamsTitle(season)}</h2>
      {content}
    </section>
  );
}

function TeamDirectory({ teams, competition, season }: { teams: readonly Team[]; competition: Competition; season: number | undefined }) {
  const { messages: m } = useI18n();
  const [query, setQuery] = useState('');
  const summaryId = useId();
  const needle = normalizeForSearch(query);
  const visible = needle === '' ? teams : teams.filter((team) => normalizeForSearch(`${team.name} ${team.code ?? ''}`).includes(needle));

  return (
    <>
      <div className={styles.search} role="search">
        <InputField
          label={m.competition.teamSearchLabel}
          type="search"
          value={query}
          autoComplete="off"
          aria-controls={summaryId}
          onChange={(event) => {
            setQuery(event.target.value);
          }}
        />
        <p id={summaryId} className={styles.summary} role="status">
          {m.competition.teamSearchSummary(visible.length, teams.length)}
        </p>
      </div>
      {visible.length === 0 ? (
        <p className="muted">{m.competition.teamNoMatches(query)}</p>
      ) : (
        <ul className={styles.teamGrid} aria-label={m.competition.teamListLabel}>
          {visible.map((team) => (
            <li key={team.id} className={styles.team}>
              <p className={styles.teamName}>
                {team.name}
                {team.code ? <span className={styles.teamCode}> {team.code}</span> : null}
              </p>
              <p className={styles.teamMeta}>
                {[team.country, team.founded === null ? null : m.competition.teamFounded(team.founded)].filter(Boolean).join(' · ')}
              </p>
              {team.is_national ? <StatusBadge tone="info">{m.competition.teamNational}</StatusBadge> : null}
              <Link
                className={styles.teamLink}
                to={fixturesHref({ competitionId: competition.id, season, teamId: team.id })}
                aria-label={m.competition.teamFixturesLabel(team.name)}
              >
                {m.competition.teamFixtures}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
