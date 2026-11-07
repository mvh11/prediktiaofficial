/**
 * DEV_ONLY deterministic fake of the five verified read-only endpoints, for
 * browser tests only. Records are synthetic (every name starts with
 * "DEV_ONLY"), mirror only the SHAPE of the backend schemas, and are served by
 * intercepting `/api/*` in the browser — no server, database or provider is
 * involved. Filtering, ordering (kickoff, then id), offset and limit follow
 * backend/app/api/routes/fixtures.py so pagination behaves like the real API.
 */
import type { Page, Route } from '@playwright/test';

export interface DevCompetition {
  id: number;
  external_id: number;
  name: string;
  type: string | null;
  country: string | null;
  country_code: string | null;
  logo_url: string | null;
  current_season: number | null;
}

export interface DevTeam {
  id: number;
  external_id: number;
  name: string;
  code: string | null;
  country: string | null;
  founded: number | null;
  is_national: boolean;
  logo_url: string | null;
}

export type DevFixture = Record<string, unknown> & {
  id: number;
  competition_id: number;
  season: number;
  kickoff_at: string;
  status_short: string;
  home_team: { id: number; name: string; logo_url: null };
  away_team: { id: number; name: string; logo_url: null };
};

export const DEV_COMPETITIONS: DevCompetition[] = [
  { id: 1, external_id: 9001, name: 'DEV_ONLY Liga Norte', type: 'League', country: 'DEV_ONLY País', country_code: null, logo_url: null, current_season: 2030 },
  { id: 2, external_id: 9002, name: 'DEV_ONLY Copa Sur', type: 'Cup', country: 'DEV_ONLY País', country_code: null, logo_url: null, current_season: 2030 },
  { id: 3, external_id: 9003, name: 'DEV_ONLY Torneo Vacío', type: null, country: null, country_code: null, logo_url: null, current_season: null },
];

function team(id: number, name: string, extra: Partial<DevTeam> = {}): DevTeam {
  return { id, external_id: 8000 + id, name, code: null, country: 'DEV_ONLY País', founded: null, is_national: false, logo_url: null, ...extra };
}

export const DEV_TEAMS: Record<number, DevTeam[]> = {
  1: [
    team(11, 'DEV_ONLY Deportes Ñandú', { code: 'DÑA', founded: 1925 }),
    team(12, 'DEV_ONLY Unión Costa'),
    team(13, 'DEV_ONLY Club Andino', { founded: 1950 }),
    team(14, 'DEV_ONLY Atlético Valle'),
  ],
  2: [team(21, 'DEV_ONLY Sur A'), team(22, 'DEV_ONLY Sur B')],
};

const FINISHED = new Set(['FT', 'AET', 'PEN']);

function fixture(id: number, competitionId: number, dayOffset: number, home: DevTeam, away: DevTeam, status: string, score: [number, number] | null): DevFixture {
  // 23:30 UTC = 20:30 in Santiago (UTC−3 in March).
  const kickoff = new Date(Date.UTC(2030, 2, 1 + dayOffset, 23, 30)).toISOString().replace('.000Z', 'Z');
  const finished = FINISHED.has(status);
  return {
    id,
    external_id: 70000 + id,
    competition_id: competitionId,
    season: 2030,
    round: `DEV_ONLY Fecha ${String(dayOffset + 1)}`,
    kickoff_at: kickoff,
    status_short: status,
    status_long: null,
    elapsed: status === '2H' ? 67 : null,
    venue_name: 'DEV_ONLY Estadio',
    venue_city: null,
    referee: null,
    home_team: { id: home.id, name: home.name, logo_url: null },
    away_team: { id: away.id, name: away.name, logo_url: null },
    home_goals: score?.[0] ?? null,
    away_goals: score?.[1] ?? null,
    halftime_home: score ? Math.min(score[0], 1) : null,
    halftime_away: score ? 0 : null,
    extratime_home: null,
    extratime_away: null,
    penalty_home: null,
    penalty_away: null,
    fulltime_home: finished && score ? score[0] : null,
    fulltime_away: finished && score ? score[1] : null,
    is_finished: finished,
  };
}

function buildFixtures(): DevFixture[] {
  const out: DevFixture[] = [];
  const league = DEV_TEAMS[1] ?? [];
  // 30 fixtures: more than one 25-row page.
  for (let index = 0; index < 30; index += 1) {
    const home = league[index % league.length] as DevTeam;
    const away = league[(index + 1) % league.length] as DevTeam;
    const status = index < 10 ? 'FT' : index === 10 ? '2H' : index === 11 ? 'PST' : 'NS';
    const score: [number, number] | null = index < 10 ? [index % 3, (index + 1) % 2] : index === 10 ? [1, 1] : null;
    out.push(fixture(101 + index, 1, index, home, away, status, score));
  }
  // Exactly 25 fixtures: one full page and nothing more.
  const cup = DEV_TEAMS[2] ?? [];
  for (let index = 0; index < 25; index += 1) {
    out.push(fixture(201 + index, 2, index, cup[index % 2] as DevTeam, cup[(index + 1) % 2] as DevTeam, 'NS', null));
  }
  return out;
}

export const DEV_FIXTURES: DevFixture[] = buildFixtures();

export interface FakeApiOptions {
  /** Milliseconds to wait before answering /fixtures requests. */
  readonly fixturesDelayMs?: number;
  /**
   * When `offline` is true every request fails like a dropped connection.
   * Needed because Playwright's context.setOffline() does not affect routes
   * fulfilled by page.route(); use both together to emulate going offline.
   */
  readonly network?: { offline: boolean };
  /** Override the response for a pathname (after the /api prefix). */
  readonly override?: Readonly<Record<string, (route: Route) => Promise<void>>>;
}

export interface RecordedRequest {
  readonly method: string;
  readonly url: URL;
}

function json(route: Route, body: unknown, status = 200): Promise<void> {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

function listFixtures(params: URLSearchParams): DevFixture[] {
  const competition = params.get('competition_id');
  const season = params.get('season');
  const teamId = params.get('team_id');
  const from = params.get('date_from');
  const to = params.get('date_to');
  const finished = params.get('finished');
  const limit = Number(params.get('limit') ?? '100');
  const offset = Number(params.get('offset') ?? '0');
  return DEV_FIXTURES.filter((item) => competition === null || item.competition_id === Number(competition))
    .filter((item) => season === null || item.season === Number(season))
    .filter((item) => teamId === null || item.home_team.id === Number(teamId) || item.away_team.id === Number(teamId))
    .filter((item) => from === null || item.kickoff_at.slice(0, 10) >= from)
    .filter((item) => to === null || item.kickoff_at.slice(0, 10) <= to)
    .filter((item) => finished === null || FINISHED.has(item.status_short) === (finished === 'true'))
    .sort((a, b) => a.kickoff_at.localeCompare(b.kickoff_at) || a.id - b.id)
    .slice(offset, offset + limit);
}

/** Serves the fake API on `/api/*` and returns the list of requests the page made. */
export async function useDevOnlyApi(page: Page, options: FakeApiOptions = {}): Promise<RecordedRequest[]> {
  const requests: RecordedRequest[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    requests.push({ method: request.method(), url });
    const path = url.pathname.replace(/^\/api/, '');
    if (options.network?.offline) return route.abort('internetdisconnected');

    const override = options.override?.[path];
    if (override) return override(route);
    if (request.method() !== 'GET') return json(route, { detail: 'Method Not Allowed' }, 405);

    if (path === '/health') return json(route, { status: 'ok', service: 'prediktia-backend' });
    if (path === '/competitions') return json(route, DEV_COMPETITIONS);

    const teams = /^\/competitions\/(\d+)\/teams$/.exec(path);
    if (teams) {
      const id = Number(teams[1]);
      if (!DEV_COMPETITIONS.some((item) => item.id === id)) return json(route, { detail: 'Competición no encontrada' }, 404);
      const season = url.searchParams.get('season');
      const list = DEV_TEAMS[id];
      if (list === undefined || (season !== null && season !== '2030')) return json(route, { detail: 'Temporada no encontrada' }, 404);
      return json(route, list);
    }

    if (path === '/fixtures') {
      if (options.fixturesDelayMs) await new Promise((resolve) => setTimeout(resolve, options.fixturesDelayMs));
      return json(route, listFixtures(url.searchParams));
    }

    const detail = /^\/fixtures\/(\d+)$/.exec(path);
    if (detail) {
      const found = DEV_FIXTURES.find((item) => item.id === Number(detail[1]));
      return found ? json(route, found) : json(route, { detail: 'Partido no encontrado' }, 404);
    }
    return json(route, { detail: 'Not Found' }, 404);
  });
  return requests;
}
