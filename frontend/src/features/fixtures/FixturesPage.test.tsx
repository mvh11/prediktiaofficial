import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { utcDay } from '../../i18n/format.ts';
import { devCompetition, devFinishedFixture, devFixture, devTeam } from '../../test/devOnlyData.ts';
import { hangingResponse, jsonResponse, mockFetch, networkFailure, requestedUrls, textResponse } from '../../test/fetchMock.ts';
import { RAW_TOKEN, withRawToken } from '../../test/rawJson.ts';
import { renderApp } from '../../test/renderApp.tsx';
import { FIXTURE_PAGE_SIZE } from './fixtureFilters.ts';

const LOOKAHEAD_LIMIT = String(FIXTURE_PAGE_SIZE + 1);
const health = () => jsonResponse({ status: 'ok', service: 'prediktia-backend' });

function happyRoutes(fixtures = [devFixture(1), devFinishedFixture(2)]) {
  return {
    '/health': health,
    '/competitions': () => jsonResponse([devCompetition(), devCompetition({ id: 2, name: 'DEV_ONLY Cup B', current_season: null, country: null, type: 'Cup' })]),
    '/competitions/1/teams': () => jsonResponse([devTeam(11), devTeam(12)]),
    '/fixtures': () => jsonResponse(fixtures),
  };
}

function fixtureRequests(fetchMock: ReturnType<typeof mockFetch>) {
  return requestedUrls(fetchMock).filter((url) => url.pathname === '/api/fixtures');
}

function results() {
  return screen.getByRole('region', { name: /^Resultados/ });
}

/** The page is code-split: wait for it to mount before querying inside it. */
function resultsLoaded() {
  return screen.findByRole('region', { name: /^Resultados/ });
}

async function rows() {
  await screen.findByText(/^Partidos \d+ a \d+, ordenados/);
  return within(results()).getAllByRole('listitem');
}

describe('FixturesPage — data source states', () => {
  it('shows an honest "not configured" state and makes no request when no API is configured', async () => {
    const fetchMock = mockFetch({});
    renderApp('/partidos', { baseUrl: null });
    expect(await within(await resultsLoaded()).findByRole('heading', { name: 'Fuente de datos no configurada' })).toBeInTheDocument();
    expect(await screen.findByText('No configurado')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reintentar' })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/^Partidos \d+ a/)).not.toBeInTheDocument();
  });

  it('shows "unavailable" when the backend cannot be reached, and recovers on retry', async () => {
    let fixtureCalls = 0;
    mockFetch({
      '/health': networkFailure,
      '/competitions': networkFailure,
      '/fixtures': () => {
        fixtureCalls += 1;
        return fixtureCalls === 1 ? networkFailure() : jsonResponse([devFixture(1)]);
      },
    });
    const { user } = renderApp('/partidos');
    expect(await within(await resultsLoaded()).findByRole('heading', { name: 'Servicio de datos no disponible' })).toBeInTheDocument();
    expect(await screen.findByText('No disponible')).toBeInTheDocument();
    expect(screen.getByLabelText('Competición')).toHaveAccessibleDescription('No fue posible cargar el listado de competiciones.');

    await user.click(within(results()).getByRole('button', { name: 'Reintentar' }));
    expect(await rows()).toHaveLength(1);
    expect(fixtureCalls).toBe(2);
  });

  it('treats a gateway error from the proxy (non-JSON 502) as unavailable', async () => {
    mockFetch({ '/health': () => textResponse('Bad Gateway', 502), '/competitions': () => textResponse('Bad Gateway', 502), '/fixtures': () => textResponse('Bad Gateway', 502) });
    renderApp('/partidos');
    const alert = await within(await resultsLoaded()).findByRole('alert');
    expect(alert).toHaveTextContent('Servicio de datos no disponible');
    expect(alert).toHaveTextContent('HTTP 502');
  });

  it('keeps an announced loading state while the backend has not answered within its time budget', async () => {
    mockFetch({ '/health': hangingResponse, '/competitions': hangingResponse, '/fixtures': hangingResponse });
    renderApp('/partidos');
    const loading = await screen.findByText('Cargando partidos…');
    expect(loading).toBeInTheDocument();
    expect(loading.closest('[role="status"]')).toHaveAttribute('aria-live', 'polite');
    expect(within(results()).queryByRole('heading', { name: 'La solicitud tardó demasiado' })).not.toBeInTheDocument();
  });

  it('shows a timeout state when the backend hangs', async () => {
    mockFetch({ '/health': hangingResponse, '/competitions': hangingResponse, '/fixtures': hangingResponse });
    renderApp('/partidos', { timeoutMs: 50 });
    // W2: with a 50 ms budget the loading state is transient. findByText only resolves once it was rendered, but
    // asserting it is *still* in the document afterwards raced the timer under CPU load; the stable loading state is
    // asserted in the previous test, and here it must be gone once the timeout is shown.
    await screen.findByText('Cargando partidos…');
    expect(await within(await resultsLoaded()).findByRole('heading', { name: 'La solicitud tardó demasiado' })).toBeInTheDocument();
    expect(within(results()).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(screen.queryByText('Cargando partidos…')).not.toBeInTheDocument();
  });

  it('rejects the whole page when one record breaks the verified schema, and says which one', async () => {
    mockFetch({ ...happyRoutes(), '/fixtures': () => jsonResponse([devFixture(1), { ...devFixture(2), status_short: '' }, devFixture(3)]) });
    renderApp('/partidos');
    const alert = await within(await resultsLoaded()).findByRole('alert');
    expect(alert).toHaveTextContent('Respuesta con formato inesperado');
    expect(alert).toHaveTextContent('registro n.º 2 de la respuesta (identificador 2)');
    expect(alert).toHaveTextContent('«status_short»');
    expect(screen.queryByText('DEV_ONLY Home 1')).not.toBeInTheDocument();
    expect(within(alert).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
  });

  it('refuses an id that JSON cannot represent exactly instead of showing a rounded one', async () => {
    const body = JSON.stringify([devFixture(1)]).replace('"id":1,', '"id":9007199254740993,');
    mockFetch({ ...happyRoutes(), '/fixtures': () => textResponse(body, 200, 'application/json') });
    renderApp('/partidos');
    const alert = await within(await resultsLoaded()).findByRole('alert');
    expect(alert).toHaveTextContent('Número no representable con exactitud');
    expect(alert).toHaveTextContent('9007199254740993');
  });

  it('shows an empty state when nothing matches', async () => {
    mockFetch({ '/health': health, '/competitions': () => jsonResponse([]), '/fixtures': () => jsonResponse([]) });
    renderApp('/partidos');
    expect(await within(await resultsLoaded()).findByRole('heading', { name: 'No se encontraron partidos' })).toBeInTheDocument();
    expect(await screen.findByText('Conectado')).toBeInTheDocument();
  });
});

describe('FixturesPage — browsing', () => {
  it('lists fixtures across competitions by Chilean day, naming the competition, with ABSTAIN on every row', async () => {
    const fetchMock = mockFetch(happyRoutes([devFixture(1), devFinishedFixture(2), devFixture(3, { kickoff_at: '2030-01-06T01:30:00Z', competition_id: 2 })]));
    renderApp('/partidos');
    const items = await rows();
    expect(items).toHaveLength(3);
    for (const row of items) expect(within(row).getByText('ABSTAIN')).toBeInTheDocument();

    // 01:30 UTC on 6 January is still Saturday 5 January in Santiago: one day group.
    expect(within(results()).getAllByRole('heading', { level: 3 })).toHaveLength(1);
    expect(within(results()).getByRole('heading', { level: 3 })).toHaveTextContent(/5 de enero de 2030/);
    expect(within(items[0] as HTMLElement).getByText('12:00')).toBeInTheDocument();
    expect(within(items[0] as HTMLElement).getByText('DEV_ONLY League A')).toBeInTheDocument();
    expect(within(items[2] as HTMLElement).getByText('DEV_ONLY Cup B')).toBeInTheDocument();
    expect(within(items[0] as HTMLElement).getByText('Programado')).toBeInTheDocument();
    expect(within(items[1] as HTMLElement).getByRole('link')).toHaveAccessibleName('DEV_ONLY Home 2 2 – 1 DEV_ONLY Away 2');

    const [request] = fixtureRequests(fetchMock);
    expect(Object.fromEntries(request?.searchParams ?? [])).toEqual({ limit: LOOKAHEAD_LIMIT, offset: '0' });
  });

  it('selecting a competition applies it immediately, with its current season and team list', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos');
    await rows();

    await user.selectOptions(screen.getByLabelText('Competición'), 'DEV_ONLY League A');

    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1');
    });
    expect(await screen.findByRole('heading', { level: 2, name: 'Resultados: DEV_ONLY League A' })).toBeInTheDocument();
    await waitFor(() => {
      expect(Object.fromEntries(fixtureRequests(fetchMock).at(-1)?.searchParams ?? [])).toEqual({
        competition_id: '1',
        season: '2030',
        limit: LOOKAHEAD_LIMIT,
        offset: '0',
      });
    });
    await waitFor(() => {
      expect(screen.getByLabelText('Equipo')).toBeEnabled();
    });
    expect(screen.getByLabelText('Temporada')).toHaveValue(2030);
  });

  it('shows a fixture-list failure without hiding the filters', async () => {
    mockFetch({ ...happyRoutes(), '/fixtures': networkFailure });
    renderApp('/partidos?competicion=1');
    expect(await within(await resultsLoaded()).findByRole('heading', { name: 'Servicio de datos no disponible' })).toBeInTheDocument();
    expect(screen.getByRole('form', { name: 'Filtros de partidos' })).toBeInTheDocument();
  });

  it('handles a competition id that is not in the catalog', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos?competicion=999');
    expect(await within(await resultsLoaded()).findByRole('heading', { name: 'Competición no encontrada' })).toBeInTheDocument();
    expect(fixtureRequests(fetchMock)).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Quitar filtro de competición' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('');
    });
  });

  it('falls back to all seasons (and says so) when a competition has no current season', async () => {
    const fetchMock = mockFetch({ ...happyRoutes(), '/competitions/2/teams': () => jsonResponse({ detail: 'Temporada no encontrada' }, 404) });
    renderApp('/partidos?competicion=2');
    expect(await screen.findByText(/no tiene temporada actual registrada; se muestran todas las temporadas/)).toBeInTheDocument();
    await rows();
    expect(fixtureRequests(fetchMock)[0]?.searchParams.has('season')).toBe(false);
    await waitFor(() => {
      expect(screen.getByLabelText('Equipo')).toHaveAccessibleDescription('No hay lista de equipos registrada para esta temporada.');
    });
  });

  it('applies filters to the URL and the request, shows removable chips, and resets', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos?competicion=1');
    await rows();
    await waitFor(() => {
      expect(screen.getByLabelText('Equipo')).toBeEnabled();
    });

    await user.selectOptions(screen.getByLabelText('Equipo'), 'DEV_ONLY Team 12');
    await user.selectOptions(screen.getByLabelText('Estado'), 'Finalizados (tiempo reglamentario, alargue o penales)');
    await user.type(screen.getByLabelText('Desde (día UTC)'), '2030-01-01');
    await user.type(screen.getByLabelText('Hasta (día UTC)'), '2030-01-31');
    await user.click(screen.getByRole('button', { name: 'Aplicar filtros' }));

    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1&temporada=2030&equipo=12&desde=2030-01-01&hasta=2030-01-31&estado=finalizados');
    });
    await waitFor(() => {
      expect(Object.fromEntries(fixtureRequests(fetchMock).at(-1)?.searchParams ?? [])).toEqual({
        competition_id: '1',
        season: '2030',
        team_id: '12',
        date_from: '2030-01-01',
        date_to: '2030-01-31',
        finished: 'true',
        limit: LOOKAHEAD_LIMIT,
        offset: '0',
      });
    });

    const chip = await screen.findByRole('button', { name: 'Quitar filtro: Equipo: DEV_ONLY Team 12' });
    await user.click(chip);
    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1&temporada=2030&desde=2030-01-01&hasta=2030-01-31&estado=finalizados');
    });

    await user.click(screen.getByRole('button', { name: 'Restablecer' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1');
    });
  });

  it('keeps a team that came from the URL when another filter is applied (team list loads after mount)', async () => {
    let releaseTeams: () => void = () => undefined;
    const teamsReady = new Promise<void>((resolve) => {
      releaseTeams = resolve;
    });
    mockFetch({ ...happyRoutes(), '/competitions/1/teams': () => teamsReady.then(() => jsonResponse([devTeam(11), devTeam(12)])) });
    const { user, router } = renderApp('/partidos?competicion=1&temporada=2030&equipo=12');
    await rows();
    expect(screen.getByLabelText('Equipo')).toHaveValue('12');
    releaseTeams();
    await waitFor(() => {
      expect(screen.getByRole('option', { name: 'DEV_ONLY Team 12' })).toBeInTheDocument();
    });
    expect(screen.getByLabelText('Equipo')).toHaveValue('12');

    await user.selectOptions(screen.getByLabelText('Estado'), 'Finalizados (tiempo reglamentario, alargue o penales)');
    await user.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1&temporada=2030&equipo=12&estado=finalizados');
    });
  });

  it('exposes a filter disclosure button for small screens that reflects its state', async () => {
    mockFetch(happyRoutes());
    const { user } = renderApp('/partidos');
    await rows();
    const toggle = screen.getByRole('button', { name: 'Mostrar filtros' });
    const form = screen.getByRole('form', { name: 'Filtros de partidos' });
    expect(toggle).toHaveAttribute('aria-controls', form.id);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(form).toHaveAttribute('data-open', 'false');
    await user.click(toggle);
    expect(screen.getByRole('button', { name: 'Ocultar filtros' })).toHaveAttribute('aria-expanded', 'true');
    expect(form).toHaveAttribute('data-open', 'true');
  });

  it('quick ranges set UTC days', async () => {
    mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos');
    await rows();
    await user.click(screen.getByRole('button', { name: 'Próximos 7 días' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe(`?desde=${utcDay()}&hasta=${utcDay(new Date(), 6)}`);
    });
  });

  it('rejects an inverted date range with an inline, described error', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos?competicion=1');
    await rows();
    const before = fixtureRequests(fetchMock).length;

    await user.type(screen.getByLabelText('Desde (día UTC)'), '2030-02-01');
    await user.type(screen.getByLabelText('Hasta (día UTC)'), '2030-01-01');
    await user.click(screen.getByRole('button', { name: 'Aplicar filtros' }));

    const to = screen.getByLabelText('Hasta (día UTC)');
    expect(to).toHaveAttribute('aria-invalid', 'true');
    expect(to).toHaveAccessibleDescription(/debe ser igual o posterior/);
    expect(router.state.location.search).toBe('?competicion=1');
    expect(fixtureRequests(fetchMock)).toHaveLength(before);
  });

  it('rejects an out-of-range season typed into the form', async () => {
    mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos?competicion=1');
    await rows();
    const season = screen.getByLabelText('Temporada');
    await user.clear(season);
    await user.type(season, '1800');
    await user.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    expect(season).toHaveAttribute('aria-invalid', 'true');
    expect(router.state.location.search).toBe('?competicion=1');
  });

  it('does not request fixtures for an inverted range typed into the URL', async () => {
    const fetchMock = mockFetch(happyRoutes());
    renderApp('/partidos?competicion=1&desde=2030-02-01&hasta=2030-01-01');
    expect(await screen.findByRole('heading', { name: 'Rango de fechas no válido' })).toBeInTheDocument();
    expect(fixtureRequests(fetchMock)).toHaveLength(0);
  });

  it('reports unusable URL parameters (including unsupported large ids) instead of silently applying them', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos?competicion=99999999999999999999&estado=raro');
    expect(await screen.findByText('Se ignoraron filtros no válidos de la dirección:')).toBeInTheDocument();
    expect(screen.getByText('competicion = «99999999999999999999» (fuera del rango admitido)')).toBeInTheDocument();
    expect(screen.getByText('estado = «raro» (valor no válido)')).toBeInTheDocument();
    await rows();
    expect(fixtureRequests(fetchMock)[0]?.searchParams.has('competition_id')).toBe(false);

    const notice = screen.getByText('Se ignoraron filtros no válidos de la dirección:').closest('[data-tone]');
    await user.click(within(notice as HTMLElement).getByRole('button', { name: 'Restablecer' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('');
    });
  });
});

describe('FixturesPage — pagination without a total count', () => {
  const page = (count: number, start = 1) => Array.from({ length: count }, (_, index) => devFixture(start + index));

  it('requests one extra row to know whether a next page exists, and never shows it', async () => {
    const fetchMock = mockFetch({
      ...happyRoutes(),
      '/fixtures': (url) => jsonResponse(url.searchParams.get('offset') === '0' ? page(FIXTURE_PAGE_SIZE + 1) : page(1, 100)),
    });
    const { user, router } = renderApp('/partidos?competicion=1');
    expect(await rows()).toHaveLength(FIXTURE_PAGE_SIZE);
    expect(screen.getByText(`Partidos 1 a ${String(FIXTURE_PAGE_SIZE)}, ordenados por fecha y hora de inicio.`)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Página anterior' })).toBeDisabled();
    expect(screen.queryByText('Fin de los resultados para estos filtros.')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Página siguiente' }));
    await screen.findByText(`Partidos ${String(FIXTURE_PAGE_SIZE + 1)} a ${String(FIXTURE_PAGE_SIZE + 1)}, ordenados por fecha y hora de inicio.`);
    expect(router.state.location.search).toBe('?competicion=1&pagina=2');
    expect(fixtureRequests(fetchMock).at(-1)?.searchParams.get('offset')).toBe(String(FIXTURE_PAGE_SIZE));
    expect(fixtureRequests(fetchMock).at(-1)?.searchParams.get('limit')).toBe(LOOKAHEAD_LIMIT);
    expect(screen.getByRole('button', { name: 'Página siguiente' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Página anterior' })).toBeEnabled();
    expect(screen.getByText('Fin de los resultados para estos filtros.')).toBeInTheDocument();
    // Focus moves to the results heading so keyboard users start at the new page.
    expect(screen.getByRole('heading', { level: 2, name: /^Resultados/ })).toHaveFocus();
  });

  it('ends an exact multiple of the page size on a full page with "Next" disabled (no empty page)', async () => {
    mockFetch({ ...happyRoutes(), '/fixtures': () => jsonResponse(page(FIXTURE_PAGE_SIZE)) });
    renderApp('/partidos?competicion=1');
    expect(await rows()).toHaveLength(FIXTURE_PAGE_SIZE);
    expect(screen.queryByRole('navigation', { name: 'Paginación' })).not.toBeInTheDocument();
    expect(screen.getByText('Fin de los resultados para estos filtros.')).toBeInTheDocument();
  });

  it('explains an empty deep-linked page and offers a way back', async () => {
    mockFetch({ ...happyRoutes(), '/fixtures': (url) => jsonResponse(url.searchParams.get('offset') === '0' ? page(3) : []) });
    const { user, router } = renderApp('/partidos?competicion=1&pagina=5');
    expect(await screen.findByRole('heading', { name: 'No hay partidos en esta página' })).toBeInTheDocument();
    expect(screen.getByText(/no informa el total de resultados/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Volver a la página anterior' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1&pagina=4');
    });
    await user.click(await screen.findByRole('button', { name: 'Ir a la primera página' }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1');
    });
    expect(await rows()).toHaveLength(3);
  });
});

describe('FixturesPage — detail round trip', () => {
  it('opens a fixture and returns to the same filters', async () => {
    mockFetch({ ...happyRoutes(), '/fixtures/2': () => jsonResponse(devFinishedFixture(2)) });
    const { user, router } = renderApp('/partidos?competicion=1&estado=finalizados');
    await rows();
    await user.click(within(results()).getByRole('link', { name: /DEV_ONLY Home 2/ }));
    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 2 vs DEV_ONLY Away 2' })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe('/partidos/2');

    await user.click(screen.getByRole('link', { name: /Volver a los partidos/ }));
    await waitFor(() => {
      expect(router.state.location.search).toBe('?competicion=1&estado=finalizados');
    });
  });
});

describe('FixturesPage — identifier integrity and date bounds', () => {
  it.each(['41.99999999999999999', '12.9999999999999999', '1.0000000000000001', '4.2e1'])(
    'refuses a list whose second row id token is %s, so no row can link to a rounded id',
    async (token) => {
      const text = JSON.stringify([devFixture(1), devFixture(42), devFixture(3)]).replace('"id":42,', `"id":${token},`);
      mockFetch({ ...happyRoutes(), '/fixtures': () => textResponse(text, 200, 'application/json') });
      renderApp('/partidos');
      const alert = await within(await resultsLoaded()).findByRole('alert');
      expect(alert).toHaveTextContent('Identificador no válido en la respuesta');
      expect(alert).toHaveTextContent('registro n.º 2');
      expect(alert).toHaveTextContent(`«${token}»`);
      expect(document.querySelectorAll('li[data-fixture-id]')).toHaveLength(0);
      expect(document.querySelector('a[href^="/partidos/"]')).toBeNull();
    },
  );

  it('reports out-of-range UTC days in the URL and never sends them (L2)', async () => {
    const fetchMock = mockFetch(happyRoutes());
    renderApp('/partidos?desde=0001-01-01&hasta=9999-12-31');
    expect(await screen.findByText('hasta = «9999-12-31» (fuera del rango admitido)')).toBeInTheDocument();
    expect(screen.getByText('desde = «0001-01-01» (fuera del rango admitido)')).toBeInTheDocument();
    await rows();
    const sent = fixtureRequests(fetchMock)[0];
    expect(sent?.searchParams.has('date_from')).toBe(false);
    expect(sent?.searchParams.has('date_to')).toBe(false);
  });

  it('rejects an out-of-range day typed into the form with a described error (L2)', async () => {
    const fetchMock = mockFetch(happyRoutes());
    const { user, router } = renderApp('/partidos');
    await rows();
    const before = fixtureRequests(fetchMock).length;
    const to = screen.getByLabelText('Hasta (día UTC)');
    expect(to).toHaveAttribute('min', '1900-01-01');
    expect(to).toHaveAttribute('max', '2100-12-31');
    await user.type(to, '9999-12-31');
    await user.click(screen.getByRole('button', { name: 'Aplicar filtros' }));
    expect(to).toHaveAttribute('aria-invalid', 'true');
    expect(to).toHaveAccessibleDescription(/entre el 1 de enero de 1900 y el 31 de diciembre de 2100/);
    expect(router.state.location.search).toBe('');
    expect(fixtureRequests(fetchMock)).toHaveLength(before);
  });
});

describe('FixturesPage — exact integers (FE2-R1)', () => {
  it.each([
    ['season', '2029.9999999999999999'],
    ['home_goals', '0.99999999999999999'],
    ['elapsed', '6.7e1'],
  ])('refuses a list whose second row has the %s token %s, in Spanish, and shows no row', async (key, token) => {
    const text = withRawToken([devFinishedFixture(1), devFinishedFixture(2, { [key]: RAW_TOKEN }), devFixture(3)], token);
    mockFetch({ ...happyRoutes(), '/fixtures': () => textResponse(text, 200, 'application/json') });
    renderApp('/partidos');
    const alert = await within(await resultsLoaded()).findByRole('alert');
    expect(alert).toHaveTextContent('Número entero no válido en la respuesta');
    expect(alert).toHaveTextContent(`El campo «${key}» de la respuesta trae «${token}», que no es un número entero exacto`);
    expect(alert).toHaveTextContent('registro n.º 2');
    expect(alert).toHaveTextContent('PREDIKTIA no elimina ni corrige datos en silencio');
    expect(within(alert).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(document.querySelectorAll('li[data-fixture-id]')).toHaveLength(0);
    expect(screen.queryByText(/DEV_ONLY Home/)).not.toBeInTheDocument();
  });

  it('accepts exact scores (including 0) and null optional integers', async () => {
    mockFetch(happyRoutes([devFinishedFixture(1, { home_goals: 0, away_goals: 3 }), devFixture(2)]));
    renderApp('/partidos');
    expect(await rows()).toHaveLength(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
