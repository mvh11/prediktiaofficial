import { createHttpClient, type HttpClient } from './httpClient.ts';
import {
  competitionListSchema,
  fixtureListSchema,
  fixtureSchema,
  healthSchema,
  teamListSchema,
  type Competition,
  type Fixture,
  type Health,
  type Team,
} from './schemas.ts';
import { appConfig, type AppConfig } from '../config/env.ts';

/** Backend limit for GET /fixtures (`limit` is validated as 1..500 server-side). */
export const MAX_FIXTURE_PAGE_SIZE = 500;

export interface FixtureQuery {
  readonly competitionId?: number | undefined;
  readonly season?: number | undefined;
  readonly teamId?: number | undefined;
  /** Inclusive UTC day, `YYYY-MM-DD`. */
  readonly dateFrom?: string | undefined;
  /** Inclusive UTC day, `YYYY-MM-DD`. */
  readonly dateTo?: string | undefined;
  /** true: finished (FT/AET/PEN) only; false: everything else. */
  readonly finished?: boolean | undefined;
  readonly limit: number;
  readonly offset: number;
}

/**
 * Read-only client for the verified PREDIKTIA backend endpoints.
 * Deliberately excluded: POST /sync/* (writes) and /providers/* (consume
 * external provider quota).
 */
export interface PrediktiaApi {
  getHealth(signal?: AbortSignal): Promise<Health>;
  listCompetitions(signal?: AbortSignal): Promise<Competition[]>;
  listCompetitionTeams(competitionId: number, season: number | undefined, signal?: AbortSignal): Promise<Team[]>;
  listFixtures(query: FixtureQuery, signal?: AbortSignal): Promise<Fixture[]>;
  getFixture(fixtureId: number, signal?: AbortSignal): Promise<Fixture>;
}

export function createPrediktiaApi(http: HttpClient): PrediktiaApi {
  return {
    getHealth: (signal) => http.getJson('/health', healthSchema, { signal }),
    listCompetitions: (signal) => http.getJson('/competitions', competitionListSchema, { signal }),
    listCompetitionTeams: (competitionId, season, signal) =>
      http.getJson(`/competitions/${competitionId}/teams`, teamListSchema, { signal, query: { season } }),
    listFixtures: (query, signal) =>
      http.getJson('/fixtures', fixtureListSchema, {
        signal,
        query: {
          competition_id: query.competitionId,
          season: query.season,
          team_id: query.teamId,
          date_from: query.dateFrom,
          date_to: query.dateTo,
          finished: query.finished,
          limit: Math.min(Math.max(1, query.limit), MAX_FIXTURE_PAGE_SIZE),
          offset: Math.max(0, query.offset),
        },
      }),
    getFixture: (fixtureId, signal) => http.getJson(`/fixtures/${fixtureId}`, fixtureSchema, { signal }),
  };
}

export function createDefaultApi(config: AppConfig = appConfig): PrediktiaApi {
  return createPrediktiaApi(createHttpClient({ baseUrl: config.apiBaseUrl, timeoutMs: config.apiTimeoutMs }));
}
