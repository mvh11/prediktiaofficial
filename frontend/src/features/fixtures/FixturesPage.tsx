import { useMemo, useRef, useState, type SubmitEvent } from 'react';
import { useSearchParams } from 'react-router';
import { useApi } from '../../api/apiContext.ts';
import { isNotFound } from '../../api/errors.ts';
import { fetchFixturePage, type FixtureQuery } from '../../api/prediktiaApi.ts';
import type { Competition, Team } from '../../api/schemas.ts';
import { PATHS } from '../../components/layout/navigation.ts';
import { ApiFailure } from '../../components/ui/ApiFailure.tsx';
import { Button } from '../../components/ui/Button.tsx';
import { InputField, SelectField } from '../../components/ui/Field.tsx';
import { Notice } from '../../components/ui/Notice.tsx';
import { PageHeader } from '../../components/ui/PageHeader.tsx';
import { EmptyState, LoadingState } from '../../components/ui/States.tsx';
import { useResource, type Resource, type ResourceState } from '../../hooks/useResource.ts';
import { utcDay } from '../../i18n/format.ts';
import { useI18n } from '../../i18n/i18n.ts';
import type { Messages } from '../../i18n/messages.es-CL.ts';
import { isIsoDay } from '../../lib/format.ts';
import { parsePositiveInt } from '../../lib/ids.ts';
import { DataServiceStatus } from '../system/DataServiceStatus.tsx';
import { groupByCountry } from '../competitions/competitionText.ts';
import { FixtureList } from './FixtureList.tsx';
import {
  FIXTURE_PAGE_SIZE,
  finishedToQuery,
  isInvertedRange,
  MAX_SEASON,
  MIN_SEASON,
  parseFinished,
  parseFixtureFilters,
  parseSeason,
  serializeFixtureFilters,
  type FixtureFilters,
  type IgnoredParam,
} from './fixtureFilters.ts';
import styles from './Fixtures.module.css';
import { cx } from '../../lib/cx.ts';

type UpdateFilters = (changes: Partial<FixtureFilters>) => void;

export function FixturesPage() {
  const { messages: m } = useI18n();
  const api = useApi();
  const [searchParams, setSearchParams] = useSearchParams();
  const { filters, ignored } = parseFixtureFilters(searchParams);
  const competitions = useResource('competitions', (signal) => api.listCompetitions(signal));
  const resultsHeadingRef = useRef<HTMLHeadingElement>(null);

  const competitionList = competitions.state.status === 'success' ? competitions.state.data : null;
  const competitionsById = useMemo(() => new Map((competitionList ?? []).map((item) => [item.id, item])), [competitionList]);
  const selected = filters.competitionId === undefined ? undefined : competitionsById.get(filters.competitionId);
  const season = filters.season ?? selected?.current_season ?? undefined;

  const teams = useResource(selected === undefined ? null : `teams:${String(selected.id)}:${String(season ?? 'current')}`, (signal) =>
    api.listCompetitionTeams(selected?.id ?? 0, season, signal),
  );

  const update: UpdateFilters = (changes) => {
    setSearchParams(serializeFixtureFilters({ ...filters, page: 1, ...changes }));
  };
  const goToPage = (page: number) => {
    update({ page });
    resultsHeadingRef.current?.focus();
  };

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: m.nav.home, to: PATHS.home }, { label: m.nav.fixtures }]}
        eyebrow={m.fixtures.eyebrow}
        title={m.fixtures.title}
        documentTitle={m.fixtures.documentTitle}
        lead={<p>{m.fixtures.lead}</p>}
        actions={<DataServiceStatus />}
      />
      <div className={cx('container', styles.page)}>
        <Notice title={m.fixtures.noticeTitle}>
          <p>{m.fixtures.notice}</p>
        </Notice>
        {ignored.length > 0 ? (
          <IgnoredParamsNotice
            ignored={ignored}
            onClean={() => {
              setSearchParams(serializeFixtureFilters(filters));
            }}
          />
        ) : null}

        <FilterPanel
          key={searchParams.toString()}
          filters={filters}
          competitions={competitions}
          selected={selected}
          season={season}
          teams={teams.state}
          update={update}
        />

        <section className={styles.results} aria-labelledby="results-title">
          <div className={styles.resultsHeader}>
            <h2 id="results-title" ref={resultsHeadingRef} tabIndex={-1}>
              {selected ? m.fixtures.resultsFor(selected.name) : m.fixtures.resultsTitle}
            </h2>
            <p className={styles.resultsNote}>{m.fixtures.timeZoneNote}</p>
          </div>
          <ResultsArea
            filters={filters}
            competitions={competitions}
            selected={selected}
            season={season}
            competitionsById={competitionsById}
            update={update}
            onPage={goToPage}
            returnSearch={searchParams.toString() ? `?${searchParams.toString()}` : ''}
          />
        </section>
      </div>
    </>
  );
}

function IgnoredParamsNotice({ ignored, onClean }: { ignored: readonly IgnoredParam[]; onClean: () => void }) {
  const { messages: m } = useI18n();
  return (
    <Notice tone="warning" title={m.ids.ignoredParams}>
      <ul className={styles.ignoredList}>
        {ignored.map((item) => (
          <li key={`${item.name}=${item.raw}`}>
            <code>{m.ids.ignoredParam(item.name, item.raw, item.reason)}</code>
          </li>
        ))}
      </ul>
      <Button variant="secondary" onClick={onClean}>
        {m.fixtures.reset}
      </Button>
    </Notice>
  );
}

interface FilterPanelProps {
  readonly filters: FixtureFilters;
  readonly competitions: Resource<Competition[]>;
  readonly selected: Competition | undefined;
  readonly season: number | undefined;
  readonly teams: ResourceState<Team[]>;
  readonly update: UpdateFilters;
}

interface FormErrors {
  readonly season?: string;
  readonly to?: string;
}

/**
 * Uncontrolled form initialised from the URL. It is remounted (via `key`) when
 * the URL changes, so browser back/forward always shows the applied filters.
 * The competition applies immediately (it determines the team list).
 */
function FilterPanel({ filters, competitions, selected, season, teams, update }: FilterPanelProps) {
  const { messages: m } = useI18n();
  const [errors, setErrors] = useState<FormErrors>({});
  // Controlled: the team list arrives after mount, and an uncontrolled <select>
  // would fall back to "all teams" when the URL's team option is replaced.
  const [teamValue, setTeamValue] = useState(filters.teamId !== undefined ? String(filters.teamId) : '');

  const onSubmit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const read = (name: string) => {
      const value = data.get(name);
      return typeof value === 'string' ? value.trim() : '';
    };
    const seasonText = read('temporada');
    const nextSeason = parseSeason(seasonText);
    const dateFrom = isIsoDay(read('desde')) ? read('desde') : undefined;
    const dateTo = isIsoDay(read('hasta')) ? read('hasta') : undefined;
    const next: { season?: string; to?: string } = {};
    if (seasonText !== '' && nextSeason === undefined) next.season = m.fixtures.seasonInvalid;
    if (isInvertedRange(dateFrom, dateTo)) next.to = m.fixtures.dateRangeError;
    if (next.season !== undefined || next.to !== undefined) {
      setErrors(next);
      return;
    }
    update({
      season: nextSeason,
      teamId: parsePositiveInt(read('equipo')),
      dateFrom,
      dateTo,
      finished: parseFinished(read('estado')) ?? 'all',
    });
  };

  const competitionField = () => {
    const state = competitions.state;
    const list = state.status === 'success' ? state.data : [];
    let hint: string = m.fixtures.competitionHint;
    if (state.status === 'loading' || state.status === 'idle') hint = m.fixtures.competitionsLoading;
    else if (state.status === 'error') hint = m.fixtures.competitionsUnavailable;
    return (
      <SelectField
        label={m.fixtures.competition}
        name="competicion"
        hint={hint}
        value={filters.competitionId !== undefined ? String(filters.competitionId) : ''}
        disabled={state.status !== 'success' && filters.competitionId === undefined}
        onChange={(event) => {
          update({ competitionId: parsePositiveInt(event.target.value), season: undefined, teamId: undefined });
        }}
      >
        <option value="">{m.fixtures.competitionAll}</option>
        {filters.competitionId !== undefined && selected === undefined ? (
          <option value={filters.competitionId}>{m.fixture.competitionFallback(filters.competitionId)}</option>
        ) : null}
        {groupByCountry(list, m).map((group) => (
          <optgroup key={group.country} label={group.country}>
            {group.competitions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </optgroup>
        ))}
      </SelectField>
    );
  };

  let teamHint: string | undefined;
  if (selected === undefined) teamHint = m.fixtures.teamNeedsCompetition;
  else if (teams.status === 'loading') teamHint = m.fixtures.teamsLoading;
  else if (teams.status === 'error') teamHint = isNotFound(teams.error) ? m.fixtures.teamsSeasonMissing : m.fixtures.teamsUnavailable;
  else if (teams.status === 'success' && teams.data.length === 0) teamHint = m.fixtures.teamsEmpty;
  const teamOptions = teams.status === 'success' ? teams.data : [];

  const today = utcDay();
  const quick = (dateFrom: string, dateTo: string) => () => {
    update({ dateFrom, dateTo });
  };

  return (
    <section className={styles.filterPanel} aria-labelledby="filters-title">
      <h2 id="filters-title" className={styles.filterTitle}>
        {m.fixtures.filtersTitle}
      </h2>
      <form className={styles.filters} onSubmit={onSubmit} aria-label={m.fixtures.filtersLabel} noValidate>
        <div className={styles.wide}>{competitionField()}</div>
        <InputField
          label={m.fixtures.season}
          name="temporada"
          type="number"
          inputMode="numeric"
          min={MIN_SEASON}
          max={MAX_SEASON}
          step={1}
          defaultValue={season ?? ''}
          hint={selected ? m.fixtures.seasonHint(selected.current_season) : m.fixtures.seasonHintNoCompetition}
          error={errors.season}
        />
        <SelectField
          label={m.fixtures.team}
          name="equipo"
          value={teamValue}
          onChange={(event) => {
            setTeamValue(event.target.value);
          }}
          hint={teamHint}
          disabled={teamOptions.length === 0 && filters.teamId === undefined}
        >
          <option value="">{m.fixtures.teamAll}</option>
          {filters.teamId !== undefined && !teamOptions.some((team) => team.id === filters.teamId) ? (
            <option value={filters.teamId}>{m.fixtures.teamUnknown(filters.teamId)}</option>
          ) : null}
          {teamOptions.map((team) => (
            <option key={team.id} value={team.id}>
              {team.name}
            </option>
          ))}
        </SelectField>
        <SelectField label={m.fixtures.status} name="estado" defaultValue={filters.finished}>
          <option value="all">{m.fixtures.statusAll}</option>
          <option value="pending">{m.fixtures.statusPending}</option>
          <option value="finished">{m.fixtures.statusFinished}</option>
        </SelectField>
        <InputField label={m.fixtures.from} name="desde" type="date" defaultValue={filters.dateFrom ?? ''} hint={m.fixtures.dateHint} />
        <InputField label={m.fixtures.to} name="hasta" type="date" defaultValue={filters.dateTo ?? ''} error={errors.to} />
        <div className={styles.quick} role="group" aria-label={m.fixtures.quickRanges}>
          <span className={styles.quickLabel} aria-hidden="true">
            {m.fixtures.quickRanges}
          </span>
          <Button variant="ghost" onClick={quick(today, today)}>
            {m.fixtures.quickToday}
          </Button>
          <Button variant="ghost" onClick={quick(today, utcDay(new Date(), 6))}>
            {m.fixtures.quickNext7}
          </Button>
          <Button variant="ghost" onClick={quick(utcDay(new Date(), -6), today)}>
            {m.fixtures.quickLast7}
          </Button>
        </div>
        <div className={styles.filterActions}>
          <Button type="submit">{m.fixtures.apply}</Button>
          <Button
            variant="secondary"
            onClick={() => {
              update({ season: undefined, teamId: undefined, dateFrom: undefined, dateTo: undefined, finished: 'all' });
            }}
          >
            {m.fixtures.reset}
          </Button>
        </div>
      </form>
      <ActiveFilters filters={filters} selected={selected} teams={teamOptions} update={update} />
    </section>
  );
}

function ActiveFilters({
  filters,
  selected,
  teams,
  update,
}: {
  filters: FixtureFilters;
  selected: Competition | undefined;
  teams: readonly Team[];
  update: UpdateFilters;
}) {
  const { messages: m, format } = useI18n();
  const chips: { key: string; label: string; clear: Partial<FixtureFilters> }[] = [];
  if (filters.competitionId !== undefined) {
    const name = selected?.name ?? m.fixture.competitionFallback(filters.competitionId);
    chips.push({ key: 'competition', label: m.fixtures.chipCompetition(name), clear: { competitionId: undefined, season: undefined, teamId: undefined } });
  }
  if (filters.season !== undefined) chips.push({ key: 'season', label: m.fixtures.chipSeason(filters.season), clear: { season: undefined } });
  if (filters.teamId !== undefined) {
    const name = teams.find((team) => team.id === filters.teamId)?.name ?? m.fixtures.teamUnknown(filters.teamId);
    chips.push({ key: 'team', label: m.fixtures.chipTeam(name), clear: { teamId: undefined } });
  }
  if (filters.finished !== 'all') {
    chips.push({
      key: 'status',
      label: filters.finished === 'finished' ? m.fixtures.chipStatusFinished : m.fixtures.chipStatusPending,
      clear: { finished: 'all' },
    });
  }
  if (filters.dateFrom !== undefined) chips.push({ key: 'from', label: m.fixtures.chipFrom(format.isoDay(filters.dateFrom)), clear: { dateFrom: undefined } });
  if (filters.dateTo !== undefined) chips.push({ key: 'to', label: m.fixtures.chipTo(format.isoDay(filters.dateTo)), clear: { dateTo: undefined } });
  if (chips.length === 0) return null;

  return (
    <div className={styles.chips}>
      <h3 className={styles.chipsTitle}>{m.fixtures.activeFilters}</h3>
      <ul className={styles.chipList}>
        {chips.map((chip) => (
          <li key={chip.key}>
            <button
              type="button"
              className={styles.chip}
              aria-label={m.fixtures.removeFilter(chip.label)}
              onClick={() => {
                update(chip.clear);
              }}
            >
              {chip.label}
              <span aria-hidden="true" className={styles.chipX}>
                ×
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface ResultsAreaProps {
  readonly filters: FixtureFilters;
  readonly competitions: Resource<Competition[]>;
  readonly selected: Competition | undefined;
  readonly season: number | undefined;
  readonly competitionsById: ReadonlyMap<number, Competition>;
  readonly update: UpdateFilters;
  readonly onPage: (page: number) => void;
  readonly returnSearch: string;
}

function ResultsArea({ filters, competitions, selected, season, competitionsById, update, onPage, returnSearch }: ResultsAreaProps) {
  const { messages: m } = useI18n();
  if (isInvertedRange(filters.dateFrom, filters.dateTo)) {
    return (
      <EmptyState title={m.fixtures.invalidRangeTitle}>
        <p>{m.fixtures.invalidRange}</p>
      </EmptyState>
    );
  }

  if (filters.competitionId !== undefined && selected === undefined) {
    // The selected competition's current season is the default, so it must be known first.
    switch (competitions.state.status) {
      case 'idle':
      case 'loading':
        return <LoadingState label={m.fixtures.competitionsLoading} rows={2} />;
      case 'error':
        return <ApiFailure error={competitions.state.error} subject={m.subjects.competitions} onRetry={competitions.reload} />;
      case 'success':
        return (
          <EmptyState
            title={m.fixtures.competitionNotFoundTitle}
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  update({ competitionId: undefined, season: undefined, teamId: undefined });
                }}
              >
                {m.fixtures.clearCompetition}
              </Button>
            }
          >
            <p>{m.fixtures.competitionNotFound}</p>
          </EmptyState>
        );
    }
  }

  const query: Omit<FixtureQuery, 'limit' | 'offset'> = {
    competitionId: filters.competitionId,
    season,
    teamId: filters.teamId,
    dateFrom: filters.dateFrom,
    dateTo: filters.dateTo,
    finished: finishedToQuery(filters.finished),
  };
  return (
    <>
      {selected !== undefined && season === undefined ? <p className="muted">{m.fixtures.allSeasonsNote}</p> : null}
      <FixtureResults
        query={query}
        page={filters.page}
        competitionsById={selected === undefined ? competitionsById : undefined}
        onPage={onPage}
        returnSearch={returnSearch}
      />
    </>
  );
}

interface FixtureResultsProps {
  readonly query: Omit<FixtureQuery, 'limit' | 'offset'>;
  readonly page: number;
  readonly competitionsById: ReadonlyMap<number, Competition> | undefined;
  readonly onPage: (page: number) => void;
  readonly returnSearch: string;
}

function FixtureResults({ query, page, competitionsById, onPage, returnSearch }: FixtureResultsProps) {
  const { messages: m } = useI18n();
  const api = useApi();
  const result = useResource(`fixtures:${JSON.stringify(query)}:${String(page)}`, (signal) =>
    fetchFixturePage(api, query, page, FIXTURE_PAGE_SIZE, signal),
  );

  switch (result.state.status) {
    case 'idle':
    case 'loading':
      return <LoadingState label={m.fixtures.loading} />;
    case 'error':
      return (
        <ApiFailure
          error={result.state.error}
          subject={m.subjects.fixtures}
          onRetry={result.reload}
          actions={
            page > 1 ? (
              <Button
                variant="ghost"
                onClick={() => {
                  onPage(1);
                }}
              >
                {m.fixtures.firstPage}
              </Button>
            ) : undefined
          }
        />
      );
    case 'success': {
      const { fixtures, hasNext } = result.state.data;
      if (fixtures.length === 0) {
        return page > 1 ? (
          <EmptyState
            title={m.fixtures.noMoreTitle}
            action={
              <>
                <Button
                  variant="secondary"
                  onClick={() => {
                    onPage(page - 1);
                  }}
                >
                  {m.fixtures.previousPageFallback}
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    onPage(1);
                  }}
                >
                  {m.fixtures.firstPage}
                </Button>
              </>
            }
          >
            <p>{m.fixtures.noMore}</p>
          </EmptyState>
        ) : (
          <EmptyState title={m.fixtures.emptyTitle}>
            <p>{m.fixtures.empty}</p>
          </EmptyState>
        );
      }
      const first = (page - 1) * FIXTURE_PAGE_SIZE + 1;
      const last = first + fixtures.length - 1;
      return (
        <>
          <p className={styles.resultsCount} role="status">
            {m.fixtures.range(first, last)}
          </p>
          <FixtureList fixtures={fixtures} competitions={competitionsById} returnSearch={returnSearch} />
          {hasNext ? null : <p className={styles.resultsNote}>{m.fixtures.lastPageNote}</p>}
          <Pagination page={page} hasNext={hasNext} onPage={onPage} m={m} />
        </>
      );
    }
  }
}

function Pagination({ page, hasNext, onPage, m }: { page: number; hasNext: boolean; onPage: (page: number) => void; m: Messages }) {
  if (page <= 1 && !hasNext) return null;
  return (
    <nav className={styles.pagination} aria-label={m.fixtures.pagination}>
      <Button
        variant="secondary"
        disabled={page <= 1}
        onClick={() => {
          onPage(page - 1);
        }}
      >
        {m.fixtures.previous}
      </Button>
      <span className={styles.pageNumber} aria-current="page">
        {m.fixtures.page(page)}
      </span>
      <Button
        variant="secondary"
        disabled={!hasNext}
        onClick={() => {
          onPage(page + 1);
        }}
      >
        {m.fixtures.next}
      </Button>
    </nav>
  );
}
