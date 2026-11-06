import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { devCompetition, devFinishedFixture, devFixture, devTeam } from '../../test/devOnlyData.ts';
import { hangingResponse, jsonResponse, mockFetch, networkFailure, requestedUrls, textResponse } from '../../test/fetchMock.ts';
import { renderApp } from '../../test/renderApp.tsx';
import { FIXTURE_PAGE_SIZE } from './fixtureFilters.ts';

const health = () => jsonResponse({ status: 'ok', service: 'prediktia-backend' });

function happyRoutes(fixtures = [devFixture(1), devFinishedFixture(2)]) {
  return {
    '/health': health,
    '/competitions': () => jsonResponse([devCompetition(), devCompetition({ id: 2, name: 'DEV_ONLY Cup B', current_season: null, country: null })]),
    '/competitions/1/teams': () => jsonResponse([devTeam(11), devTeam(12)]),
    '/fixtures': () => jsonResponse(fixtures),
  };
}

function fixtureRequests(fetchMock: ReturnType<typeof mockFetch>) {
  return requestedUrls(fetchMock).filter((url) => url.pathname === '/api/fixtures');
}

describe('FixturesPage — data source states', () => {
  it('shows an honest "not configured" state and makes no request when no API is configured', async () => {
    const fetchMock = mockFetch({});
    renderApp('/fixtures', { baseUrl: null });
    expect(await screen.findByRole('heading', { name: 'Data source not configured' })).toBeInTheDocument();
    expect(screen.getByText('Not configured')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole('list', { name: 'Fixtures' })).not.toBeInTheDocument();
  });

  it('shows "unavailable" when the backend cannot be reached, and recovers on retry', async () => {
    let competitionsCalls = 0;
    mockFetch({
      '/health': networkFailure,
      '/competitions': () => {
        competitionsCalls += 1;
        return competitionsCalls === 1 ? networkFailure() : jsonResponse([devCompetition()]);
      },
    });
    const { user } = renderApp('/fixtures');
    expect(await screen.findByRole('heading', { name: 'Data service unavailable' })).toBeInTheDocument();
    expect(await screen.findByText('Unavailable')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { name: 'Choose a competition' })).toBeInTheDocument();
    expect(competitionsCalls).toBe(2);
  });

  it('treats a gateway error from the proxy (non-JSON 502) as unavailable', async () => {
    mockFetch({ '/health': () => textResponse('Bad Gateway', 502), '/competitions': () => textResponse('Bad Gateway', 502) });
    renderApp('/fixtures');
    expect(await screen.findByRole('heading', { name: 'Data service unavailable' })).toBeInTheDocument();
  });

  it('shows a timeout state when the backend hangs', async () => {
    mockFetch({ '/health': hangingResponse, '/competitions': hangingResponse });
    renderApp('/fixtures', { timeoutMs: 50 });
    expect(await screen.findByText('Loading competitions…')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Request timed out' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('refuses to render a response that does not match the verified schema', async () => {
    mockFetch({ '/health': health, '/competitions': () => jsonResponse([{ id: 'not-a-number' }]) });
    renderApp('/fixtures');
    expect(await screen.findByRole('heading', { name: 'Unexpected response' })).toBeInTheDocument();
  });

  it('shows an empty state when no competitions exist', async () => {
    mockFetch({ '/health': health, '/competitions': () => jsonResponse([]) });
    renderApp('/fixtures');
    expect(await screen.findByRole('heading', { name: 'No competitions available yet' })).toBeInTheDocument();
    expect(await screen.findByText('Connected')).toBeInTheDocument();
  });
});

describe('FixturesPage — browsing', () => {
  it('asks for a competition, then lists its fixtures for the current season with ABSTAIN on every row', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/fixtures');

    expect(await screen.findByRole('heading', { name: 'Choose a competition' })).toBeInTheDocument();
    expect(fixtureRequests(fetchMock)).toHaveLength(0);

    await user.selectOptions(screen.getByLabelText('Competition'), 'DEV_ONLY League A (DEV_ONLY Country)');

    const list = await screen.findByRole('list', { name: 'Fixtures' });
    const rows = within(list).getAllByRole('listitem');
    expect(rows).toHaveLength(2);
    for (const row of rows) expect(within(row).getByText('ABSTAIN')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).getByText('Not started')).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByRole('link')).toHaveTextContent('DEV_ONLY Home 22 – 1DEV_ONLY Away 2');
    expect(screen.getByRole('heading', { level: 2, name: /DEV_ONLY League A/ })).toHaveTextContent('DEV_ONLY League A · Season 2030');
    expect(router.state.location.search).toBe('?competition=1');

    const [request] = fixtureRequests(fetchMock);
    expect(Object.fromEntries(request?.searchParams ?? [])).toEqual({ competition_id: '1', season: '2030', limit: String(FIXTURE_PAGE_SIZE), offset: '0' });
  });

  it('shows the empty state when no fixtures match', async () => {
    mockFetch(happyRoutes([]));
    renderApp('/fixtures?competition=1');
    expect(await screen.findByRole('heading', { name: 'No fixtures found' })).toBeInTheDocument();
  });

  it('shows a fixture-list failure without hiding the filters', async () => {
    mockFetch({ ...happyRoutes(), '/fixtures': networkFailure });
    renderApp('/fixtures?competition=1');
    expect(await screen.findByRole('heading', { name: 'Data service unavailable' })).toBeInTheDocument();
    expect(screen.getByRole('form', { name: 'Fixture filters' })).toBeInTheDocument();
  });

  it('handles a competition id that is not in the catalog', async () => {
    mockFetch(happyRoutes());
    const { user, router } = renderApp('/fixtures?competition=999');
    expect(await screen.findByRole('heading', { name: 'Competition not found' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear selection' }));
    expect(router.state.location.search).toBe('');
  });

  it('falls back to all seasons (and says so) when a competition has no current season', async () => {
    const fetchMock = mockFetch({ ...happyRoutes(), '/competitions/2/teams': () => jsonResponse({ detail: 'Temporada no encontrada' }, 404) });
    renderApp('/fixtures?competition=2');
    expect(await screen.findByText('No current season is recorded; showing all seasons.')).toBeInTheDocument();
    await screen.findByRole('list', { name: 'Fixtures' });
    expect(fixtureRequests(fetchMock)[0]?.searchParams.has('season')).toBe(false);
    expect(await screen.findByText('No team list is recorded for this season.')).toBeInTheDocument();
  });

  it('applies filters to the URL and the request', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/fixtures?competition=1');
    await screen.findByRole('list', { name: 'Fixtures' });
    await waitFor(() => {
      expect(screen.getByLabelText('Team')).toBeEnabled();
    });

    await user.selectOptions(screen.getByLabelText('Team'), 'DEV_ONLY Team 12');
    await user.selectOptions(screen.getByLabelText('Status'), 'Finished');
    await user.type(screen.getByLabelText('From (UTC)'), '2030-01-01');
    await user.type(screen.getByLabelText('To (UTC)'), '2030-01-31');
    await user.click(screen.getByRole('button', { name: 'Apply filters' }));

    await waitFor(() => {
      expect(router.state.location.search).toBe('?competition=1&season=2030&team=12&from=2030-01-01&to=2030-01-31&status=finished');
    });
    await waitFor(() => {
      expect(fixtureRequests(fetchMock).length).toBeGreaterThanOrEqual(2);
    });
    const last = fixtureRequests(fetchMock).at(-1);
    expect(Object.fromEntries(last?.searchParams ?? [])).toEqual({
      competition_id: '1',
      season: '2030',
      team_id: '12',
      date_from: '2030-01-01',
      date_to: '2030-01-31',
      finished: 'true',
      limit: String(FIXTURE_PAGE_SIZE),
      offset: '0',
    });

    await user.click(screen.getByRole('button', { name: 'Reset' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?competition=1');
    });
  });

  it('rejects an inverted date range with an inline, described error', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/fixtures?competition=1');
    await screen.findByRole('list', { name: 'Fixtures' });
    const before = fixtureRequests(fetchMock).length;

    await user.type(screen.getByLabelText('From (UTC)'), '2030-02-01');
    await user.type(screen.getByLabelText('To (UTC)'), '2030-01-01');
    await user.click(screen.getByRole('button', { name: 'Apply filters' }));

    const to = screen.getByLabelText('To (UTC)');
    expect(to).toHaveAttribute('aria-invalid', 'true');
    expect(to).toHaveAccessibleDescription(/must be on or after/);
    expect(router.state.location.search).toBe('?competition=1');
    expect(fixtureRequests(fetchMock)).toHaveLength(before);
  });

  it('does not request fixtures for an inverted range typed into the URL', async () => {
    const fetchMock = mockFetch(happyRoutes());
    renderApp('/fixtures?competition=1&from=2030-02-01&to=2030-01-01');
    expect(await screen.findByRole('heading', { name: 'Invalid date range' })).toBeInTheDocument();
    expect(fixtureRequests(fetchMock)).toHaveLength(0);
  });

  it('paginates with offset and disables "Next" on a short page', async () => {
    const fullPage = Array.from({ length: FIXTURE_PAGE_SIZE }, (_, index) => devFixture(index + 1));
    const fetchMock = mockFetch({
      ...happyRoutes(),
      '/fixtures': (url) => jsonResponse(url.searchParams.get('offset') === '0' ? fullPage : [devFixture(100)]),
    });
    const { user, router } = renderApp('/fixtures?competition=1');
    await screen.findByText(`Showing fixtures 1–${FIXTURE_PAGE_SIZE}, ordered by kickoff.`);
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: 'Next page' }));
    await screen.findByText(`Showing fixtures ${FIXTURE_PAGE_SIZE + 1}–${FIXTURE_PAGE_SIZE + 1}, ordered by kickoff.`);
    expect(router.state.location.search).toBe('?competition=1&page=2');
    expect(fixtureRequests(fetchMock).at(-1)?.searchParams.get('offset')).toBe(String(FIXTURE_PAGE_SIZE));
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled();
  });

  it('opens a fixture detail from the list', async () => {
    mockFetch({ ...happyRoutes(), '/fixtures/2': () => jsonResponse(devFinishedFixture(2)) });
    const { user, router } = renderApp('/fixtures?competition=1');
    const list = await screen.findByRole('list', { name: 'Fixtures' });
    await user.click(within(list).getByRole('link', { name: /DEV_ONLY Home 2/ }));
    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 2 vs DEV_ONLY Away 2' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/fixtures/2');
  });
});
