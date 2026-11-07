import { describe, expect, it } from 'vitest';
import { MAX_DB_INT } from '../lib/ids.ts';
import { devCompetition, devFinishedFixture, devFixture, devTeam } from '../test/devOnlyData.ts';
import { competitionSchema, fixtureSchema, teamSchema } from './schemas.ts';

describe('fixtureSchema', () => {
  it('accepts the verified FixtureOut shape', () => {
    expect(fixtureSchema.safeParse(devFixture(1)).success).toBe(true);
    expect(fixtureSchema.safeParse(devFinishedFixture(2)).success).toBe(true);
  });

  it.each(['2030-01-05T15:00:00Z', '2030-01-05T15:00:00.123456+00:00', '2030-01-05T15:00-03:00'])('accepts zoned timestamp %s', (kickoff) => {
    expect(fixtureSchema.safeParse(devFixture(1, { kickoff_at: kickoff })).success).toBe(true);
  });

  it.each(['2030-01-05T15:00:00', '2030-01-05', 'not a date', '2030-13-45T99:99:00Z'])('rejects ambiguous or invalid timestamp %s', (kickoff) => {
    expect(fixtureSchema.safeParse(devFixture(1, { kickoff_at: kickoff })).success).toBe(false);
  });

  it('rejects negative or fractional goals', () => {
    expect(fixtureSchema.safeParse(devFixture(1, { home_goals: -1 })).success).toBe(false);
    expect(fixtureSchema.safeParse(devFixture(1, { home_goals: 1.5 })).success).toBe(false);
  });

  it('rejects a response missing required fields', () => {
    const { is_finished: _omitted, ...partial } = devFixture(1);
    expect(fixtureSchema.safeParse(partial).success).toBe(false);
  });

  it('accepts ids up to the PostgreSQL integer maximum and rejects anything beyond (no rounding)', () => {
    const teams = { home_team: { id: 1, name: 'h', logo_url: null }, away_team: { id: 2, name: 'a', logo_url: null } };
    expect(fixtureSchema.safeParse(devFixture(MAX_DB_INT, teams)).success).toBe(true);
    expect(fixtureSchema.safeParse(devFixture(MAX_DB_INT + 1, teams)).success).toBe(false);
    expect(fixtureSchema.safeParse(devFixture(2 ** 53, teams)).success).toBe(false);
    expect(fixtureSchema.safeParse(devFixture(1, { away_team: { id: MAX_DB_INT + 1, name: 'a', logo_url: null } })).success).toBe(false);
    expect(fixtureSchema.safeParse(devFixture(1, { competition_id: 0 })).success).toBe(false);
    expect(fixtureSchema.safeParse(devFixture(1, { home_team: { id: 2.5, name: 'x', logo_url: null } })).success).toBe(false);
  });
});

describe('catalog schemas', () => {
  it('accept the verified CompetitionOut and TeamOut shapes', () => {
    expect(competitionSchema.safeParse(devCompetition()).success).toBe(true);
    expect(competitionSchema.safeParse(devCompetition({ current_season: null, country: null, type: null })).success).toBe(true);
    expect(teamSchema.safeParse(devTeam(3, { founded: 1899, is_national: true })).success).toBe(true);
  });

  it('reject empty names and out-of-range ids', () => {
    expect(competitionSchema.safeParse(devCompetition({ name: '' })).success).toBe(false);
    expect(teamSchema.safeParse(devTeam(MAX_DB_INT + 1)).success).toBe(false);
  });
});
