import { describe, expect, it } from 'vitest';
import { translateLegacySearch } from '../../app/legacyUrls.ts';
import { esCL } from '../../i18n/messages.es-CL.ts';
import { isIsoDay, normalizeForSearch } from '../../lib/format.ts';
import { devFinishedFixture, devFixture } from '../../test/devOnlyData.ts';
import {
  finishedToQuery,
  fixturesHref,
  isInvertedRange,
  parseFixtureFilters,
  serializeFixtureFilters,
} from './fixtureFilters.ts';
import { fixturePhase, fixtureStatusLabel, hasFinalScore, headlineScore, scoreLines } from './fixtureStatus.ts';

describe('fixture status', () => {
  it.each([
    ['NS', 'scheduled'],
    ['1H', 'in_play'],
    ['FT', 'finished'],
    ['AET', 'finished'],
    ['PEN', 'finished'],
    ['AWD', 'awarded'],
    ['WO', 'awarded'],
    ['PST', 'postponed'],
    ['CANC', 'cancelled'],
    ['ZZZ', 'unknown'],
  ])('%s is %s', (code, phase) => {
    expect(fixturePhase(code)).toBe(phase);
  });

  it('labels statuses in Spanish and falls back to the stored long status, then the raw code', () => {
    expect(fixtureStatusLabel({ status_short: 'FT', status_long: 'Match Finished' }, esCL)).toBe('Finalizado');
    expect(fixtureStatusLabel({ status_short: 'PST', status_long: null }, esCL)).toBe('Postergado');
    expect(fixtureStatusLabel({ status_short: 'AET', status_long: null }, esCL)).toBe('Finalizado tras alargue');
    expect(fixtureStatusLabel({ status_short: 'ZZZ', status_long: 'Provider text' }, esCL)).toBe('Provider text');
    expect(fixtureStatusLabel({ status_short: 'ZZZ', status_long: null }, esCL)).toBe('ZZZ');
  });

  it('shows a headline score only for final statuses with a complete pair', () => {
    expect(headlineScore(devFixture(1))).toBeNull();
    expect(headlineScore(devFixture(1, { status_short: '2H', home_goals: 1, away_goals: 0 }))).toBeNull();
    expect(headlineScore(devFinishedFixture(1, { away_goals: null }))).toBeNull();
    expect(headlineScore(devFinishedFixture(1))).toEqual({ kind: 'final', home: 2, away: 1 });
  });

  it('lists only complete score pairs, never inferring missing values', () => {
    const lines = scoreLines(devFinishedFixture(1, { halftime_away: null }));
    expect(lines.map((line) => line.kind)).toEqual(['final', 'fulltime']);
    expect(scoreLines(devFixture(1))).toEqual([]);
  });

  it.each(['1H', 'HT', '2H', 'ET', 'P', 'LIVE', 'ABD', 'SUSP', 'INT', 'PST', 'CANC', 'NS', 'TBD', 'ZZZ'])(
    'never labels a %s score as final',
    (code) => {
      const lines = scoreLines(devFixture(1, { status_short: code, home_goals: 1, away_goals: 0 }));
      expect(lines).toEqual([{ kind: 'lastSync', home: 1, away: 0 }]);
      expect(hasFinalScore({ status_short: code })).toBe(false);
    },
  );

  it.each(['FT', 'AET', 'PEN', 'AWD', 'WO'])('labels a %s score as final', (code) => {
    expect(scoreLines(devFinishedFixture(1, { status_short: code }))[0]?.kind).toBe('final');
  });
});

describe('fixture filters (Spanish URL parameters)', () => {
  it('round-trips through the URL query string', () => {
    const query = 'competicion=3&temporada=2030&equipo=7&desde=2030-01-01&hasta=2030-02-01&estado=finalizados&pagina=2';
    const { filters, ignored } = parseFixtureFilters(new URLSearchParams(query));
    expect(filters).toEqual({ competitionId: 3, season: 2030, teamId: 7, dateFrom: '2030-01-01', dateTo: '2030-02-01', finished: 'finished', page: 2 });
    expect(ignored).toEqual([]);
    expect(serializeFixtureFilters(filters).toString()).toBe(query);
  });

  it('ignores malformed parameters instead of guessing, and reports each one', () => {
    const { filters, ignored } = parseFixtureFilters(
      new URLSearchParams('competicion=-1&temporada=99&equipo=x&desde=2030-02-30&estado=bogus&pagina=0'),
    );
    expect(filters).toEqual({ competitionId: undefined, season: undefined, teamId: undefined, dateFrom: undefined, dateTo: undefined, finished: 'all', page: 1 });
    expect(ignored.map((item) => `${item.name}:${item.reason}`)).toEqual([
      'temporada:invalid',
      'estado:invalid',
      'competicion:invalid',
      'equipo:invalid',
      'desde:invalid',
      'pagina:invalid',
    ]);
  });

  it('reports out-of-range ids as unsupported and never rounds them', () => {
    const { filters, ignored } = parseFixtureFilters(new URLSearchParams('competicion=99999999999999999999&equipo=2147483648'));
    expect(filters.competitionId).toBeUndefined();
    expect(filters.teamId).toBeUndefined();
    expect(ignored).toEqual([
      { name: 'competicion', raw: '99999999999999999999', reason: 'unsupported' },
      { name: 'equipo', raw: '2147483648', reason: 'unsupported' },
    ]);
  });

  it('builds deep links', () => {
    expect(fixturesHref({})).toBe('/partidos');
    expect(fixturesHref({ competitionId: 4, season: 2030, teamId: 9 })).toBe('/partidos?competicion=4&temporada=2030&equipo=9');
    expect(fixturesHref({ competitionId: 4, finished: 'pending', dateFrom: '2030-01-01' })).toBe('/partidos?competicion=4&desde=2030-01-01&estado=pendientes');
  });

  it('detects an inverted date range', () => {
    expect(isInvertedRange('2030-02-01', '2030-01-01')).toBe(true);
    expect(isInvertedRange('2030-01-01', '2030-01-01')).toBe(false);
    expect(isInvertedRange(undefined, '2030-01-01')).toBe(false);
  });

  it('maps the status filter to the backend `finished` parameter', () => {
    expect(finishedToQuery('all')).toBeUndefined();
    expect(finishedToQuery('finished')).toBe(true);
    expect(finishedToQuery('pending')).toBe(false);
  });

  it('translates Phase 1 (English) query strings to the Spanish parameters', () => {
    expect(translateLegacySearch('?competition=1&season=2030&team=12&from=2030-01-01&to=2030-01-31&status=finished&page=2')).toBe(
      '?competicion=1&temporada=2030&equipo=12&desde=2030-01-01&hasta=2030-01-31&estado=finalizados&pagina=2',
    );
    expect(translateLegacySearch('')).toBe('');
  });
});

describe('format helpers', () => {
  it('validates real calendar days', () => {
    expect(isIsoDay('2030-02-28')).toBe(true);
    expect(isIsoDay('2030-02-29')).toBe(false);
    expect(isIsoDay('2030-2-1')).toBe(false);
  });

  it('normalizes search text (case and diacritics)', () => {
    expect(normalizeForSearch('  Ñublense ÚNION  ')).toBe('nublense union');
    expect(normalizeForSearch('Peñarol')).toContain('penarol');
  });
});
