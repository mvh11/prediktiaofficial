import { isIsoDay } from '../../lib/format.ts';
import { parseId } from '../../lib/ids.ts';

export type FinishedFilter = 'all' | 'pending' | 'finished';

/** Filter state for the fixture browser. The URL query string is the source of truth. */
export interface FixtureFilters {
  readonly competitionId: number | undefined;
  readonly season: number | undefined;
  readonly teamId: number | undefined;
  readonly dateFrom: string | undefined;
  readonly dateTo: string | undefined;
  readonly finished: FinishedFilter;
  readonly page: number;
}

/** URL parameter names (Spanish, user-facing). */
export const FILTER_PARAMS = {
  competition: 'competicion',
  season: 'temporada',
  team: 'equipo',
  from: 'desde',
  to: 'hasta',
  status: 'estado',
  page: 'pagina',
} as const;

const STATUS_VALUES: Readonly<Record<Exclude<FinishedFilter, 'all'>, string>> = { pending: 'pendientes', finished: 'finalizados' };

/** A URL parameter that was present but could not be used; shown to the user, never silently applied. */
export interface IgnoredParam {
  readonly name: string;
  readonly raw: string;
  readonly reason: 'invalid' | 'unsupported';
}

export const FIXTURE_PAGE_SIZE = 25;
export const MIN_SEASON = 1900;
export const MAX_SEASON = 2100;
/**
 * Supported UTC days for date filters. The backend computes `date_to + 1 day`
 * (backend/app/api/routes/fixtures.py), which overflows for 9999-12-31 and
 * answers HTTP 500; days outside the season range are never sent.
 */
export const MIN_DAY = '1900-01-01';
export const MAX_DAY = '2100-12-31';

/** A real `YYYY-MM-DD` calendar day within [MIN_DAY, MAX_DAY]. */
export function isSupportedDay(value: string): boolean {
  return isIsoDay(value) && value >= MIN_DAY && value <= MAX_DAY;
}

export const EMPTY_FILTERS: FixtureFilters = {
  competitionId: undefined,
  season: undefined,
  teamId: undefined,
  dateFrom: undefined,
  dateTo: undefined,
  finished: 'all',
  page: 1,
};

export function parseSeason(value: string | null | undefined): number | undefined {
  const parsed = parseId(value);
  return parsed.kind === 'valid' && parsed.value >= MIN_SEASON && parsed.value <= MAX_SEASON ? parsed.value : undefined;
}

export function parseFinished(value: string | null): FinishedFilter | undefined {
  if (value === STATUS_VALUES.pending || value === 'pending') return 'pending';
  if (value === STATUS_VALUES.finished || value === 'finished') return 'finished';
  if (value === 'all' || value === 'todos') return 'all';
  return undefined;
}

export interface ParsedFilters {
  readonly filters: FixtureFilters;
  readonly ignored: readonly IgnoredParam[];
}

export function parseFixtureFilters(params: URLSearchParams): ParsedFilters {
  const ignored: IgnoredParam[] = [];
  const read = (name: string) => {
    const raw = params.get(name);
    return raw === null || raw === '' ? null : raw;
  };

  const id = (name: string): number | undefined => {
    const raw = read(name);
    if (raw === null) return undefined;
    const parsed = parseId(raw);
    if (parsed.kind === 'valid') return parsed.value;
    ignored.push({ name, raw, reason: parsed.kind });
    return undefined;
  };

  const seasonRaw = read(FILTER_PARAMS.season);
  const season = parseSeason(seasonRaw);
  if (seasonRaw !== null && season === undefined) ignored.push({ name: FILTER_PARAMS.season, raw: seasonRaw, reason: 'invalid' });

  const day = (name: string): string | undefined => {
    const raw = read(name);
    if (raw === null) return undefined;
    if (isSupportedDay(raw)) return raw;
    ignored.push({ name, raw, reason: isIsoDay(raw) ? 'unsupported' : 'invalid' });
    return undefined;
  };

  const statusRaw = read(FILTER_PARAMS.status);
  const finished = statusRaw === null ? 'all' : parseFinished(statusRaw);
  if (statusRaw !== null && finished === undefined) ignored.push({ name: FILTER_PARAMS.status, raw: statusRaw, reason: 'invalid' });

  const competitionId = id(FILTER_PARAMS.competition);
  const teamId = id(FILTER_PARAMS.team);
  const dateFrom = day(FILTER_PARAMS.from);
  const dateTo = day(FILTER_PARAMS.to);
  const page = id(FILTER_PARAMS.page) ?? 1;

  return {
    filters: { competitionId, season, teamId, dateFrom, dateTo, finished: finished ?? 'all', page },
    ignored,
  };
}

export function serializeFixtureFilters(filters: FixtureFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.competitionId !== undefined) params.set(FILTER_PARAMS.competition, String(filters.competitionId));
  if (filters.season !== undefined) params.set(FILTER_PARAMS.season, String(filters.season));
  if (filters.teamId !== undefined) params.set(FILTER_PARAMS.team, String(filters.teamId));
  if (filters.dateFrom !== undefined) params.set(FILTER_PARAMS.from, filters.dateFrom);
  if (filters.dateTo !== undefined) params.set(FILTER_PARAMS.to, filters.dateTo);
  if (filters.finished !== 'all') params.set(FILTER_PARAMS.status, STATUS_VALUES[filters.finished]);
  if (filters.page > 1) params.set(FILTER_PARAMS.page, String(filters.page));
  return params;
}

/** `/partidos?…` for the given filters (used for deep links from other pages). */
export function fixturesHref(filters: Partial<FixtureFilters>): string {
  const search = serializeFixtureFilters({ ...EMPTY_FILTERS, ...filters }).toString();
  return search ? `/partidos?${search}` : '/partidos';
}

/** True when the date range is inverted. */
export function isInvertedRange(dateFrom: string | undefined, dateTo: string | undefined): boolean {
  return dateFrom !== undefined && dateTo !== undefined && dateFrom > dateTo;
}

export function finishedToQuery(value: FinishedFilter): boolean | undefined {
  if (value === 'finished') return true;
  if (value === 'pending') return false;
  return undefined;
}
