import { z } from 'zod';

/**
 * Runtime schemas mirroring the VERIFIED backend response models
 * (backend/app/schemas/catalog.py and backend/app/schemas/fixture.py at the
 * authorized base commit). Unknown extra fields are stripped; missing or
 * mistyped fields reject the whole response rather than rendering guesses.
 *
 * Deliberately STRICTER than the backend models (fail-closed, not a contract
 * mismatch): names must be non-empty (backend: `str`) and goal counts must be
 * non-negative integers (backend: `int | None`). A single row violating this
 * rejects the whole response as `invalid_response` instead of rendering a
 * nonsensical value.
 */

const id = z.number().int().positive();
const goals = z.number().int().nonnegative().nullable();

/** ISO-8601 timestamp that carries an explicit UTC offset (never a naive local time). */
const zonedTimestamp = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/, 'Expected a timestamp with a UTC offset')
  .refine((value) => !Number.isNaN(Date.parse(value)), 'Unparseable timestamp');

/** GET /health */
export const healthSchema = z.object({
  status: z.literal('ok'),
  service: z.string(),
});

/** GET /competitions -> list[CompetitionOut] */
export const competitionSchema = z.object({
  id,
  external_id: z.number().int(),
  name: z.string().min(1),
  type: z.string().nullable(),
  country: z.string().nullable(),
  country_code: z.string().nullable(),
  logo_url: z.string().nullable(),
  current_season: z.number().int().nullable(),
});
export const competitionListSchema = z.array(competitionSchema);

/** GET /competitions/{id}/teams -> list[TeamOut] */
export const teamSchema = z.object({
  id,
  external_id: z.number().int(),
  name: z.string().min(1),
  code: z.string().nullable(),
  country: z.string().nullable(),
  founded: z.number().int().nullable(),
  is_national: z.boolean(),
  logo_url: z.string().nullable(),
});
export const teamListSchema = z.array(teamSchema);

const fixtureTeamSchema = z.object({
  id,
  name: z.string().min(1),
  logo_url: z.string().nullable(),
});

/** GET /fixtures -> list[FixtureOut]; GET /fixtures/{id} -> FixtureOut */
export const fixtureSchema = z.object({
  id,
  external_id: z.number().int(),
  competition_id: id,
  season: z.number().int(),
  round: z.string().nullable(),
  kickoff_at: zonedTimestamp,
  status_short: z.string().min(1),
  status_long: z.string().nullable(),
  elapsed: z.number().int().nullable(),
  venue_name: z.string().nullable(),
  venue_city: z.string().nullable(),
  referee: z.string().nullable(),
  home_team: fixtureTeamSchema,
  away_team: fixtureTeamSchema,
  home_goals: goals,
  away_goals: goals,
  halftime_home: goals,
  halftime_away: goals,
  extratime_home: goals,
  extratime_away: goals,
  penalty_home: goals,
  penalty_away: goals,
  fulltime_home: goals,
  fulltime_away: goals,
  is_finished: z.boolean(),
});
export const fixtureListSchema = z.array(fixtureSchema);

export type Health = z.infer<typeof healthSchema>;
export type Competition = z.infer<typeof competitionSchema>;
export type Team = z.infer<typeof teamSchema>;
export type Fixture = z.infer<typeof fixtureSchema>;
