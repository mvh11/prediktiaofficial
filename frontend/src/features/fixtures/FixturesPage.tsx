import { useState, type SubmitEvent } from 'react';
import { useSearchParams } from 'react-router';
import { useApi } from '../../api/apiContext.ts';
import { isNotFound } from '../../api/errors.ts';
import type { Competition, Fixture, Team } from '../../api/schemas.ts';
import { ApiFailure } from '../../components/ui/ApiFailure.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { InputField, SelectField } from '../../components/ui/Field.tsx';
import { Notice } from '../../components/ui/Notice.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, LoadingState } from '../../components/ui/States.tsx';
import { useResource, type ResourceState } from '../../hooks/useResource.ts';
import { isIsoDay, parsePositiveInt } from '../../lib/format.ts';
import { DataServiceStatus } from '../system/DataServiceStatus.tsx';
import { FixtureList } from './FixtureList.tsx';
import {
  dateRangeError,
  FIXTURE_PAGE_SIZE,
  finishedToQuery,
  MAX_SEASON,
  MIN_SEASON,
  parseFinished,
  parseFixtureFilters,
  parseSeason,
  serializeFixtureFilters,
  type FixtureFilters,
} from './fixtureFilters.ts';
import styles from './Fixtures.module.css';
import { cx } from '../../lib/cx.ts';

type UpdateFilters = (changes: Partial<FixtureFilters>) => void;

function competitionLabel(competition: Competition): string {
  return competition.country ? `${competition.name} (${competition.country})` : competition.name;
}

export function FixturesPage() {
  const api = useApi();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = parseFixtureFilters(searchParams);
  const competitions = useResource('competitions', (signal) => api.listCompetitions(signal));

  const update: UpdateFilters = (changes) => {
    setSearchParams(serializeFixtureFilters({ ...filters, page: 1, ...changes }));
  };

  let content;
  switch (competitions.state.status) {
    case 'idle':
    case 'loading':
      content = <LoadingState label="Loading competitions…" rows={2} />;
      break;
    case 'error':
      content = <ApiFailure error={competitions.state.error} subject="the competition list" onRetry={competitions.reload} headingLevel={2} />;
      break;
    case 'success':
      content =
        competitions.state.data.length === 0 ? (
          <EmptyState title="No competitions available yet" headingLevel={2}>
            <p>The PREDIKTIA data store does not contain any competitions at the moment, so there are no fixtures to browse.</p>
          </EmptyState>
        ) : (
          <FixtureBrowser competitions={competitions.state.data} filters={filters} update={update} formKey={searchParams.toString()} />
        );
      break;
  }

  return (
    <>
      <PageHeader
        eyebrow="Fixtures"
        title="Fixture browser"
        lead={<p>Competitions and matches stored by PREDIKTIA, shown exactly as last synchronized.</p>}
        actions={<DataServiceStatus />}
      />
      <div className={cx('container', styles.page)}>
        <Notice title="What you are looking at">
          <p>
            Fixture data comes from PREDIKTIA’s data store, not a live feed: statuses and scores are as of the last
            synchronization. No predictions, odds or recommendations are attached to any fixture.
          </p>
        </Notice>
        {content}
      </div>
    </>
  );
}

interface FixtureBrowserProps {
  readonly competitions: readonly Competition[];
  readonly filters: FixtureFilters;
  readonly update: UpdateFilters;
  readonly formKey: string;
}

function FixtureBrowser({ competitions, filters, update, formKey }: FixtureBrowserProps) {
  const competition = competitions.find((item) => item.id === filters.competitionId);

  return (
    <>
      <div className={styles.competitionPicker}>
        <SelectField
          label="Competition"
          value={competition ? String(competition.id) : ''}
          onChange={(event) => {
            update({ competitionId: parsePositiveInt(event.target.value), season: undefined, teamId: undefined });
          }}
        >
          <option value="">Select a competition…</option>
          {competitions.map((item) => (
            <option key={item.id} value={item.id}>
              {competitionLabel(item)}
            </option>
          ))}
        </SelectField>
      </div>

      {filters.competitionId === undefined ? (
        <EmptyState title="Choose a competition" headingLevel={2}>
          <p>Select a competition above to browse its fixtures.</p>
        </EmptyState>
      ) : competition === undefined ? (
        <EmptyState
          title="Competition not found"
          headingLevel={2}
          action={
            <Button
              variant="secondary"
              onClick={() => {
                update({ competitionId: undefined, season: undefined, teamId: undefined });
              }}
            >
              Clear selection
            </Button>
          }
        >
          <p>The requested competition is not in the PREDIKTIA catalog.</p>
        </EmptyState>
      ) : (
        <CompetitionFixtures key={competition.id} competition={competition} filters={filters} update={update} formKey={formKey} />
      )}
    </>
  );
}

interface CompetitionFixturesProps {
  readonly competition: Competition;
  readonly filters: FixtureFilters;
  readonly update: UpdateFilters;
  readonly formKey: string;
}

function CompetitionFixtures({ competition, filters, update, formKey }: CompetitionFixturesProps) {
  const api = useApi();
  const season = filters.season ?? competition.current_season ?? undefined;
  const rangeError = dateRangeError(filters.dateFrom, filters.dateTo);

  const teams = useResource(`teams:${competition.id}:${season ?? 'current'}`, (signal) =>
    api.listCompetitionTeams(competition.id, season, signal),
  );

  const query = {
    competitionId: competition.id,
    season,
    teamId: filters.teamId,
    dateFrom: filters.dateFrom,
    dateTo: filters.dateTo,
    finished: finishedToQuery(filters.finished),
    limit: FIXTURE_PAGE_SIZE,
    offset: (filters.page - 1) * FIXTURE_PAGE_SIZE,
  };
  const fixtures = useResource(rangeError === null ? `fixtures:${JSON.stringify(query)}` : null, (signal) =>
    api.listFixtures(query, signal),
  );

  return (
    <>
      <FilterForm key={formKey} filters={filters} season={season} teams={teams.state} update={update} />

      <section className={styles.results} aria-labelledby="results-title">
        <div className={styles.resultsHeader}>
          <h2 id="results-title">
            {competition.name}
            {season !== undefined ? <span className={styles.season}> · Season {season}</span> : null}
          </h2>
          {season === undefined ? <p className="muted">No current season is recorded; showing all seasons.</p> : null}
        </div>
        <Results
          state={fixtures.state}
          rangeError={rangeError}
          page={filters.page}
          onRetry={fixtures.reload}
          onPage={(page) => {
            update({ page });
          }}
        />
      </section>
    </>
  );
}

interface FilterFormProps {
  readonly filters: FixtureFilters;
  readonly season: number | undefined;
  readonly teams: ResourceState<Team[]>;
  readonly update: UpdateFilters;
}

/**
 * Uncontrolled form initialised from the URL. It is remounted (via `key`) when
 * the URL changes, so browser back/forward always shows the applied filters.
 */
function FilterForm({ filters, season, teams, update }: FilterFormProps) {
  const [error, setError] = useState<string | null>(null);

  const onSubmit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const read = (name: string) => {
      const value = data.get(name);
      return typeof value === 'string' ? value.trim() : '';
    };
    const dateFrom = isIsoDay(read('from')) ? read('from') : undefined;
    const dateTo = isIsoDay(read('to')) ? read('to') : undefined;
    const problem = dateRangeError(dateFrom, dateTo);
    if (problem !== null) {
      setError(problem);
      return;
    }
    update({
      season: parseSeason(read('season')),
      teamId: parsePositiveInt(read('team')),
      dateFrom,
      dateTo,
      finished: parseFinished(read('status')),
    });
  };

  const onReset = () => {
    update({ season: undefined, teamId: undefined, dateFrom: undefined, dateTo: undefined, finished: 'all' });
  };

  let teamHint: string | undefined;
  if (teams.status === 'loading') teamHint = 'Loading teams…';
  else if (teams.status === 'error') {
    teamHint = isNotFound(teams.error) ? 'No team list is recorded for this season.' : 'The team list is unavailable right now.';
  } else if (teams.status === 'success' && teams.data.length === 0) teamHint = 'No teams are recorded for this season.';
  const teamOptions = teams.status === 'success' ? teams.data : [];

  return (
    <form className={styles.filters} onSubmit={onSubmit} aria-label="Fixture filters" noValidate>
      <InputField
        label="Season"
        name="season"
        type="number"
        inputMode="numeric"
        min={MIN_SEASON}
        max={MAX_SEASON}
        step={1}
        defaultValue={season ?? ''}
        hint="Defaults to the current season."
      />
      <SelectField
        label="Team"
        name="team"
        defaultValue={filters.teamId !== undefined ? String(filters.teamId) : ''}
        hint={teamHint}
        disabled={teamOptions.length === 0 && filters.teamId === undefined}
      >
        <option value="">All teams</option>
        {filters.teamId !== undefined && !teamOptions.some((team) => team.id === filters.teamId) ? (
          <option value={filters.teamId}>Team #{filters.teamId}</option>
        ) : null}
        {teamOptions.map((team) => (
          <option key={team.id} value={team.id}>
            {team.name}
          </option>
        ))}
      </SelectField>
      <SelectField label="Status" name="status" defaultValue={filters.finished}>
        <option value="all">All fixtures</option>
        <option value="pending">Not finished (incl. postponed, cancelled, awarded)</option>
        <option value="finished">Finished</option>
      </SelectField>
      <InputField label="From (UTC)" name="from" type="date" defaultValue={filters.dateFrom ?? ''} />
      <InputField label="To (UTC)" name="to" type="date" defaultValue={filters.dateTo ?? ''} error={error ?? undefined} />
      <div className={styles.filterActions}>
        <Button type="submit">Apply filters</Button>
        <Button type="button" variant="ghost" onClick={onReset}>
          Reset
        </Button>
      </div>
    </form>
  );
}

interface ResultsProps {
  readonly state: ResourceState<Fixture[]>;
  readonly rangeError: string | null;
  readonly page: number;
  readonly onRetry: () => void;
  readonly onPage: (page: number) => void;
}

function Results({ state, rangeError, page, onRetry, onPage }: ResultsProps) {
  if (rangeError !== null) {
    return (
      <EmptyState title="Invalid date range">
        <p>{rangeError} Adjust the dates and apply the filters again.</p>
      </EmptyState>
    );
  }
  switch (state.status) {
    case 'idle':
    case 'loading':
      return <LoadingState label="Loading fixtures…" />;
    case 'error':
      return <ApiFailure error={state.error} subject="the fixture list" onRetry={onRetry} />;
    case 'success': {
      const fixtures = state.data;
      if (fixtures.length === 0) {
        return page > 1 ? (
          <EmptyState
            title="No more fixtures"
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  onPage(1);
                }}
              >
                Back to first page
              </Button>
            }
          >
            <p>There are no fixtures on this page for the selected filters.</p>
          </EmptyState>
        ) : (
          <EmptyState title="No fixtures found">
            <p>No fixtures in the PREDIKTIA data store match these filters. Try another season, team, status or date range.</p>
          </EmptyState>
        );
      }
      const first = (page - 1) * FIXTURE_PAGE_SIZE + 1;
      const last = first + fixtures.length - 1;
      const hasNext = fixtures.length === FIXTURE_PAGE_SIZE;
      return (
        <>
          <p className={styles.resultsCount} role="status">
            Showing fixtures {first}–{last}, ordered by kickoff.
          </p>
          <FixtureList fixtures={fixtures} label="Fixtures" />
          <nav className={styles.pagination} aria-label="Pagination">
            <Button
              variant="secondary"
              disabled={page <= 1}
              onClick={() => {
                onPage(page - 1);
              }}
            >
              Previous page
            </Button>
            <span className={styles.pageNumber}>Page {page}</span>
            <Button
              variant="secondary"
              disabled={!hasNext}
              onClick={() => {
                onPage(page + 1);
              }}
            >
              Next page
            </Button>
          </nav>
        </>
      );
    }
  }
}
