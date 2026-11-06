/**
 * DEV_ONLY synthetic records for automated tests.
 *
 * These objects only mirror the SHAPE of the verified backend responses. They
 * are not real competitions, teams, fixtures or results, every name is
 * prefixed with "DEV_ONLY", and this module lives under src/test/ so it is
 * never imported by application code or shipped in the production bundle.
 */
import type { Competition, Fixture, Team } from '../api/schemas.ts';

export const DEV_ONLY_PREFIX = 'DEV_ONLY';

export function devCompetition(overrides: Partial<Competition> = {}): Competition {
  return {
    id: 1,
    external_id: 900001,
    name: `${DEV_ONLY_PREFIX} League A`,
    type: 'League',
    country: `${DEV_ONLY_PREFIX} Country`,
    country_code: null,
    logo_url: null,
    current_season: 2030,
    ...overrides,
  };
}

export function devTeam(id: number, overrides: Partial<Team> = {}): Team {
  return {
    id,
    external_id: 800000 + id,
    name: `${DEV_ONLY_PREFIX} Team ${id}`,
    code: null,
    country: null,
    founded: null,
    is_national: false,
    logo_url: null,
    ...overrides,
  };
}

/** A scheduled (not started) synthetic fixture with no score. */
export function devFixture(id: number, overrides: Partial<Fixture> = {}): Fixture {
  return {
    id,
    external_id: 700000 + id,
    competition_id: 1,
    season: 2030,
    round: `${DEV_ONLY_PREFIX} Round 1`,
    kickoff_at: '2030-01-05T15:00:00Z',
    status_short: 'NS',
    status_long: 'Not Started',
    elapsed: null,
    venue_name: null,
    venue_city: null,
    referee: null,
    home_team: { id: 10 + id, name: `${DEV_ONLY_PREFIX} Home ${id}`, logo_url: null },
    away_team: { id: 20 + id, name: `${DEV_ONLY_PREFIX} Away ${id}`, logo_url: null },
    home_goals: null,
    away_goals: null,
    halftime_home: null,
    halftime_away: null,
    extratime_home: null,
    extratime_away: null,
    penalty_home: null,
    penalty_away: null,
    fulltime_home: null,
    fulltime_away: null,
    is_finished: false,
    ...overrides,
  };
}

/** A synthetic finished fixture with complete score pairs (shape test only). */
export function devFinishedFixture(id: number, overrides: Partial<Fixture> = {}): Fixture {
  return devFixture(id, {
    status_short: 'FT',
    status_long: 'Match Finished',
    home_goals: 2,
    away_goals: 1,
    halftime_home: 1,
    halftime_away: 0,
    fulltime_home: 2,
    fulltime_away: 1,
    is_finished: true,
    ...overrides,
  });
}
