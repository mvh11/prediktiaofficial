import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { utcDay } from '../../i18n/format.ts';
import { devCompetition, devFixture, devTeam } from '../../test/devOnlyData.ts';
import { jsonResponse, mockFetch, networkFailure, requestedUrls } from '../../test/fetchMock.ts';
import { renderApp } from '../../test/renderApp.tsx';

const health = () => jsonResponse({ status: 'ok', service: 'prediktia-backend' });
const CATALOG = [
  devCompetition({ id: 1, name: 'DEV_ONLY Liga Ñuble', country: 'DEV_ONLY Chile', type: 'League' }),
  devCompetition({ id: 2, name: 'DEV_ONLY Copa Austral', country: 'DEV_ONLY Chile', type: 'Cup', current_season: null }),
  devCompetition({ id: 3, name: 'DEV_ONLY Torneo Mundial', country: null, type: null }),
];

describe('CompetitionsPage', () => {
  it('groups the catalog by country and links to each competition and its fixtures', async () => {
    mockFetch({ '/health': health, '/competitions': () => jsonResponse(CATALOG) });
    renderApp('/competiciones');
    expect(await screen.findByRole('heading', { level: 1, name: 'Competiciones' })).toBeInTheDocument();
    const chile = await screen.findByRole('region', { name: 'Competiciones de DEV_ONLY Chile' });
    expect(within(chile).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('region', { name: 'Competiciones de Sin país registrado' })).toBeInTheDocument();

    expect(within(chile).getByRole('link', { name: 'DEV_ONLY Liga Ñuble' })).toHaveAttribute('href', '/competiciones/1');
    expect(within(chile).getByRole('link', { name: 'Ver partidos · DEV_ONLY Liga Ñuble' })).toHaveAttribute('href', '/partidos?competicion=1');
    expect(within(chile).getByText('Liga')).toBeInTheDocument();
    expect(within(chile).getByText('Copa')).toBeInTheDocument();
    expect(within(chile).getByText('Temporada actual: 2030')).toBeInTheDocument();
    expect(within(chile).getByText('Sin temporada actual registrada')).toBeInTheDocument();
    expect(screen.getByText('3 competiciones')).toBeInTheDocument();
  });

  it('searches the complete list ignoring case and accents, keeping the query in the URL', async () => {
    mockFetch({ '/health': health, '/competitions': () => jsonResponse(CATALOG) });
    const { user, router } = renderApp('/competiciones');
    const search = await screen.findByRole('searchbox', { name: 'Buscar competición' });
    await user.type(search, 'nuble');
    expect(screen.getByText('1 de 3 competiciones')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'DEV_ONLY Liga Ñuble' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'DEV_ONLY Copa Austral' })).not.toBeInTheDocument();
    expect(router.state.location.search).toBe('?buscar=nuble');

    await user.clear(search);
    await user.type(search, 'zzz');
    expect(screen.getByRole('heading', { name: 'Ninguna competición coincide con la búsqueda' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Limpiar búsqueda' }));
    expect(screen.getByText('3 competiciones')).toBeInTheDocument();
    expect(router.state.location.search).toBe('');
  });

  it('shows an empty catalog honestly', async () => {
    mockFetch({ '/health': health, '/competitions': () => jsonResponse([]) });
    renderApp('/competiciones');
    expect(await screen.findByRole('heading', { name: 'Aún no hay competiciones registradas' })).toBeInTheDocument();
  });

  it('shows an unavailable state with retry', async () => {
    let calls = 0;
    mockFetch({
      '/health': networkFailure,
      '/competitions': () => {
        calls += 1;
        return calls === 1 ? networkFailure() : jsonResponse(CATALOG);
      },
    });
    const { user } = renderApp('/competiciones');
    expect(await screen.findByRole('heading', { name: 'Servicio de datos no disponible' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByRole('link', { name: 'DEV_ONLY Liga Ñuble' })).toBeInTheDocument();
  });
});

describe('CompetitionDetailPage', () => {
  function routes(overrides: Record<string, (url: URL) => Response> = {}) {
    return {
      '/health': health,
      '/competitions': () => jsonResponse(CATALOG),
      '/competitions/1/teams': (url: URL) =>
        url.searchParams.get('season') === '2029'
          ? jsonResponse({ detail: 'Temporada no encontrada' }, 404)
          : jsonResponse([devTeam(11, { name: 'DEV_ONLY Deportes Ñandú', code: 'DEN', founded: 1925, country: 'DEV_ONLY Chile' }), devTeam(12, { is_national: true })]),
      '/fixtures': () => jsonResponse([devFixture(1)]),
      ...overrides,
    };
  }

  it('shows facts, upcoming fixtures and the team directory with deep links', async () => {
    const fetchMock = mockFetch(routes());
    renderApp('/competiciones/1');
    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Liga Ñuble' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Ruta de navegación' })).toHaveTextContent('InicioCompeticionesDEV_ONLY Liga Ñuble');
    const facts = screen.getByRole('region', { name: 'Datos de la competición' });
    expect(within(facts).getByText('Liga')).toBeInTheDocument();
    expect(within(facts).getByText('2030')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ver partidos de la temporada 2030' })).toHaveAttribute('href', '/partidos?competicion=1&temporada=2030');

    const teams = await screen.findByRole('list', { name: 'Equipos' });
    expect(within(teams).getAllByRole('listitem')).toHaveLength(2);
    expect(within(teams).getByText('Fundado en 1925', { exact: false })).toBeInTheDocument();
    expect(within(teams).getByText('Selección nacional')).toBeInTheDocument();
    expect(within(teams).getByRole('link', { name: 'Partidos de DEV_ONLY Deportes Ñandú' })).toHaveAttribute(
      'href',
      '/partidos?competicion=1&temporada=2030&equipo=11',
    );

    expect(await screen.findByRole('link', { name: /DEV_ONLY Home 1/ })).toHaveAttribute('href', '/partidos/1');
    const upcoming = requestedUrls(fetchMock).find((url) => url.pathname === '/api/fixtures');
    expect(Object.fromEntries(upcoming?.searchParams ?? [])).toEqual({
      competition_id: '1',
      season: '2030',
      date_from: utcDay(),
      finished: 'false',
      limit: '7',
      offset: '0',
    });
  });

  it('filters teams without accents and changes the season through the URL', async () => {
    const fetchMock = mockFetch(routes());
    const { user, router } = renderApp('/competiciones/1');
    const search = await screen.findByRole('searchbox', { name: 'Buscar equipo' });
    await user.type(search, 'nandu');
    expect(screen.getByText('1 de 2 equipos')).toBeInTheDocument();

    const season = screen.getByLabelText('Temporada');
    await user.clear(season);
    await user.type(season, '2029');
    await user.click(screen.getByRole('button', { name: 'Ver temporada' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?temporada=2029');
    });
    expect(await screen.findByRole('heading', { name: 'Temporada no registrada' })).toBeInTheDocument();
    expect(screen.getByText('No hay registro de la temporada 2029 para esta competición.')).toBeInTheDocument();
    expect(requestedUrls(fetchMock).some((url) => url.pathname === '/api/competitions/1/teams' && url.searchParams.get('season') === '2029')).toBe(true);
  });

  it('explains a competition without a current season', async () => {
    mockFetch(routes({ '/competitions/2/teams': () => jsonResponse({ detail: 'Temporada no encontrada' }, 404), '/fixtures': () => jsonResponse([]) }));
    renderApp('/competiciones/2');
    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Copa Austral' })).toBeInTheDocument();
    expect(await screen.findByText(/no tiene una temporada actual registrada/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ver partidos de todas las temporadas' })).toHaveAttribute('href', '/partidos?competicion=2');
    expect(await screen.findByRole('heading', { name: 'Sin próximos partidos registrados' })).toBeInTheDocument();
  });

  it('reports an unknown competition', async () => {
    mockFetch(routes());
    renderApp('/competiciones/77');
    expect(await screen.findByRole('heading', { level: 1, name: 'Competición no encontrada' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ver todas las competiciones' })).toHaveAttribute('href', '/competiciones');
  });

  it.each([
    ['abc', 'Identificador no válido'],
    ['99999999999999999999', 'Identificador fuera del rango admitido'],
  ])('rejects the id %s without any request', async (raw, heading) => {
    const fetchMock = mockFetch({});
    renderApp(`/competiciones/${raw}`);
    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
