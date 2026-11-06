import { describe, expect, it } from 'vitest';
import { isIsoDay, parsePositiveInt } from '../../lib/format.ts';
import { devFinishedFixture, devFixture } from '../../test/devOnlyData.ts';
import { dateRangeError, finishedToQuery, parseFixtureFilters, serializeFixtureFilters } from './fixtureFilters.ts';
import { fixturePhase, fixtureStatusLabel, headlineScore, scoreLines } from './fixtureStatus.ts';

describe('fixture status', () => {
  it.each([
    ['NS', 'scheduled'],
    ['1H', 'in_play'],
    ['FT', 'finished'],
    ['AET', 'finished'],
    ['PEN', 'finished'],
    ['AWD', 'awarded'],
    ['PST', 'postponed'],
    ['CANC', 'cancelled'],
    ['ZZZ', 'unknown'],
  ])('%s is %s', (code, phase) => {
    expect(fixturePhase(code)).toBe(phase);
  });

  it('falls back to the stored long status, then the raw code, for unknown codes', () => {
    expect(fixtureStatusLabel({ status_short: 'ZZZ', status_long: 'Provider text' })).toBe('Provider text');
    expect(fixtureStatusLabel({ status_short: 'ZZZ', status_long: null })).toBe('ZZZ');
  });

  it('shows a headline score only for finished fixtures with a complete pair', () => {
    expect(headlineScore(devFixture(1))).toBeNull();
    expect(headlineScore(devFixture(1, { status_short: '2H', home_goals: 1, away_goals: 0 }))).toBeNull();
    expect(headlineScore(devFinishedFixture(1, { away_goals: null }))).toBeNull();
    expect(headlineScore(devFinishedFixture(1))).toEqual({ label: 'Final score', home: 2, away: 1 });
  });

  it('lists only complete score pairs, never inferring missing values', () => {
    const lines = scoreLines(devFinishedFixture(1, { halftime_away: null }));
    expect(lines.map((line) => line.label)).toEqual(['Final score', '90 minutes']);
    expect(scoreLines(devFixture(1))).toEqual([]);
  });
});

describe('fixture filters', () => {
  it('round-trips through the URL query string', () => {
    const filters = parseFixtureFilters(new URLSearchParams('competition=3&season=2030&team=7&from=2030-01-01&to=2030-02-01&status=finished&page=2'));
    expect(filters).toEqual({ competitionId: 3, season: 2030, teamId: 7, dateFrom: '2030-01-01', dateTo: '2030-02-01', finished: 'finished', page: 2 });
    expect(serializeFixtureFilters(filters).toString()).toBe('competition=3&season=2030&team=7&from=2030-01-01&to=2030-02-01&status=finished&page=2');
  });

  it('ignores malformed parameters instead of guessing', () => {
    expect(parseFixtureFilters(new URLSearchParams('competition=-1&season=99&team=x&from=2030-02-30&status=bogus&page=0'))).toEqual({
      competitionId: undefined,
      season: undefined,
      teamId: undefined,
      dateFrom: undefined,
      dateTo: undefined,
      finished: 'all',
      page: 1,
    });
  });

  it('validates the date range', () => {
    expect(dateRangeError('2030-02-01', '2030-01-01')).toMatch(/on or after/);
    expect(dateRangeError('2030-01-01', '2030-01-01')).toBeNull();
    expect(dateRangeError(undefined, '2030-01-01')).toBeNull();
  });

  it('maps the status filter to the backend `finished` parameter', () => {
    expect(finishedToQuery('all')).toBeUndefined();
    expect(finishedToQuery('finished')).toBe(true);
    expect(finishedToQuery('pending')).toBe(false);
  });
});

describe('format helpers', () => {
  it('validates real calendar days', () => {
    expect(isIsoDay('2030-02-28')).toBe(true);
    expect(isIsoDay('2030-02-29')).toBe(false);
    expect(isIsoDay('2030-2-1')).toBe(false);
  });

  it('parses strictly positive integers', () => {
    expect(parsePositiveInt('42')).toBe(42);
    expect(parsePositiveInt('0')).toBeUndefined();
    expect(parsePositiveInt('1e3')).toBeUndefined();
    expect(parsePositiveInt('9999999999')).toBeUndefined();
    expect(parsePositiveInt(undefined)).toBeUndefined();
  });
});
