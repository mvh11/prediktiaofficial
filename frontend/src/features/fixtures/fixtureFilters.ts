import { isIsoDay, parsePositiveInt } from '../../lib/format.ts';

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

export const FIXTURE_PAGE_SIZE = 25;
export const MIN_SEASON = 1900;
export const MAX_SEASON = 2100;

export function parseSeason(value: string | null | undefined): number | undefined {
  const parsed = parsePositiveInt(value);
  return parsed !== undefined && parsed >= MIN_SEASON && parsed <= MAX_SEASON ? parsed : undefined;
}

export function parseFinished(value: string | null): FinishedFilter {
  return value === 'pending' || value === 'finished' ? value : 'all';
}

function parseDay(value: string | null): string | undefined {
  return value !== null && isIsoDay(value) ? value : undefined;
}

export function parseFixtureFilters(params: URLSearchParams): FixtureFilters {
  return {
    competitionId: parsePositiveInt(params.get('competition')),
    season: parseSeason(params.get('season')),
    teamId: parsePositiveInt(params.get('team')),
    dateFrom: parseDay(params.get('from')),
    dateTo: parseDay(params.get('to')),
    finished: parseFinished(params.get('status')),
    page: parsePositiveInt(params.get('page')) ?? 1,
  };
}

export function serializeFixtureFilters(filters: FixtureFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.competitionId !== undefined) params.set('competition', String(filters.competitionId));
  if (filters.season !== undefined) params.set('season', String(filters.season));
  if (filters.teamId !== undefined) params.set('team', String(filters.teamId));
  if (filters.dateFrom !== undefined) params.set('from', filters.dateFrom);
  if (filters.dateTo !== undefined) params.set('to', filters.dateTo);
  if (filters.finished !== 'all') params.set('status', filters.finished);
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** Returns a message when the date range is inverted, otherwise null. */
export function dateRangeError(dateFrom: string | undefined, dateTo: string | undefined): string | null {
  if (dateFrom !== undefined && dateTo !== undefined && dateFrom > dateTo) {
    return 'The “to” date must be on or after the “from” date.';
  }
  return null;
}

export function finishedToQuery(value: FinishedFilter): boolean | undefined {
  if (value === 'finished') return true;
  if (value === 'pending') return false;
  return undefined;
}
