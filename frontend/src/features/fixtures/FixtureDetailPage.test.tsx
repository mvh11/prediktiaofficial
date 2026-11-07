import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { devCompetition, devFinishedFixture, devFixture } from '../../test/devOnlyData.ts';
import { jsonResponse, mockFetch, networkFailure, textResponse } from '../../test/fetchMock.ts';
import { renderApp } from '../../test/renderApp.tsx';

const competitions = () => jsonResponse([devCompetition()]);

describe('FixtureDetailPage', () => {
  it('shows stored details in Chile and UTC time, all complete score pairs and the research context', async () => {
    mockFetch({
      '/competitions': competitions,
      '/fixtures/7': () =>
        jsonResponse(
          devFinishedFixture(7, {
            status_short: 'AET',
            home_goals: 3,
            away_goals: 2,
            extratime_home: 1,
            extratime_away: 1,
            fulltime_home: 2,
            fulltime_away: 1,
            venue_name: 'DEV_ONLY Estadio',
            venue_city: 'DEV_ONLY Ciudad',
          }),
        ),
    });
    renderApp('/partidos/7');

    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 7 vs DEV_ONLY Away 7' })).toBeInTheDocument();
    expect(document.title).toBe('DEV_ONLY Home 7 vs DEV_ONLY Away 7 · PREDIKTIA');
    expect(screen.getAllByText('Finalizado tras alargue').length).toBeGreaterThan(0);

    const table = screen.getByRole('table', { name: /Marcadores registrados/ });
    const scoreRows = within(table).getAllByRole('row').slice(1).map((row) => row.textContent);
    expect(scoreRows).toEqual(['Resultado final32', '90 minutos21', 'Primer tiempo10', 'Alargue11']);

    const details = screen.getByRole('region', { name: 'Datos del partido' });
    expect(within(details).getByText(/5 ene/)).toBeInTheDocument();
    expect(within(details).getByText(/12:00/)).toBeInTheDocument();
    expect(within(details).getByText('2030-01-05 15:00 UTC')).toBeInTheDocument();
    expect(within(details).getByText('DEV_ONLY Estadio, DEV_ONLY Ciudad')).toBeInTheDocument();
    expect(within(details).getByText('No registrado')).toBeInTheDocument();
    expect(await within(details).findByRole('link', { name: 'DEV_ONLY League A' })).toHaveAttribute('href', '/competiciones/1');
    expect(screen.getByText(/La API no informa la hora de esa sincronización/)).toBeInTheDocument();

    const output = screen.getByRole('region', { name: 'Salida del modelo' });
    expect(within(output).getByText('ABSTAIN')).toBeInTheDocument();
    expect(within(output).getByText('NOT_CERTIFIED')).toBeInTheDocument();
    expect(within(output).getByText('WAIT_REAL_SOURCE')).toBeInTheDocument();
    expect(within(output).getByRole('link', { name: 'Por qué PREDIKTIA se abstiene' })).toHaveAttribute('href', '/metodologia#abstencion');
    expect(screen.getByText(/Para este partido está registrado: 2-1\./)).toBeInTheDocument();

    const more = screen.getByRole('navigation', { name: 'Seguir explorando' });
    expect(within(more).getByRole('link', { name: 'Más partidos de DEV_ONLY League A, temporada 2030' })).toHaveAttribute(
      'href',
      '/partidos?competicion=1&temporada=2030',
    );
    expect(within(more).getByRole('link', { name: 'Partidos de DEV_ONLY Home 7 en esta temporada' })).toHaveAttribute(
      'href',
      '/partidos?competicion=1&temporada=2030&equipo=17',
    );
  });

  it.each([
    ['2H', 'Segundo tiempo', 'minuto 60 registrado'],
    ['ABD', 'Abandonado', null],
    ['SUSP', 'Suspendido', null],
  ] as const)('labels a %s score as the last-synchronized score, never as final', async (code, statusLabel, liveNote) => {
    mockFetch({
      '/competitions': competitions,
      '/fixtures/5': () => jsonResponse(devFixture(5, { status_short: code, home_goals: 1, away_goals: 0, halftime_home: 1, halftime_away: 0, elapsed: 60 })),
    });
    renderApp('/partidos/5');
    const table = await screen.findByRole('table', { name: /Marcadores registrados/ });
    expect(screen.getAllByText(statusLabel).length).toBeGreaterThan(0);
    const scoreRows = within(table).getAllByRole('row').slice(1).map((row) => row.textContent);
    expect(scoreRows).toEqual(['Marcador en la última sincronización10', 'Primer tiempo10']);
    expect(screen.queryByText('Resultado final')).not.toBeInTheDocument();
    expect(screen.getByText(/el marcador mostrado no es un resultado final/)).toBeInTheDocument();
    if (liveNote) {
      expect(screen.getByText(new RegExp(liveNote))).toBeInTheDocument();
      expect(screen.getByText(/No es un marcador en vivo\./)).toBeInTheDocument();
    }
    expect(screen.getByText(/no está registrado para este partido/)).toBeInTheDocument();
  });

  it('still shows the fixture when the competition list is unavailable', async () => {
    mockFetch({ '/competitions': networkFailure, '/fixtures/9': () => jsonResponse(devFixture(9)) });
    renderApp('/partidos/9');
    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 9 vs DEV_ONLY Away 9' })).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Competición n.º 1' })).toHaveAttribute('href', '/competiciones/1');
  });

  it('accepts 10-digit ids within the backend integer range', async () => {
    mockFetch({ '/competitions': competitions, '/fixtures/2000000000': () => jsonResponse(devFixture(2_000_000_000)) });
    renderApp('/partidos/2000000000');
    expect(await screen.findByRole('heading', { level: 1, name: /DEV_ONLY Home 2000000000/ })).toBeInTheDocument();
  });

  it.each(['99999999999999999999', '2147483648', '9007199254740993'])('reports the out-of-range id %s and makes no request', async (raw) => {
    const fetchMock = mockFetch({});
    renderApp(`/partidos/${raw}`);
    expect(await screen.findByRole('heading', { level: 1, name: 'Identificador fuera del rango admitido' })).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`El identificador ${raw} supera el máximo admitido`))).toBeInTheDocument();
    expect(screen.getByText(/2\.147\.483\.647/)).toBeInTheDocument();
    expect(screen.getByText(/no se redondeó ni se recortó/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(['abc', '007', '0', '-3', '1.5'])('reports the invalid id %s and makes no request', async (raw) => {
    const fetchMock = mockFetch({});
    renderApp(`/partidos/${raw}`);
    expect(await screen.findByRole('heading', { level: 1, name: 'Identificador no válido' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ver partidos' })).toHaveAttribute('href', '/partidos');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows a not-found state for a 404', async () => {
    mockFetch({ '/competitions': competitions, '/fixtures/404': () => jsonResponse({ detail: 'Partido no encontrado' }, 404) });
    renderApp('/partidos/404');
    expect(await screen.findByRole('heading', { level: 1, name: 'Partido no encontrado' })).toBeInTheDocument();
  });

  it('refuses a detail response whose id cannot be represented exactly', async () => {
    const body = JSON.stringify(devFixture(1)).replace('"external_id":700001', '"external_id":12345678901234567890');
    mockFetch({ '/competitions': competitions, '/fixtures/1': () => textResponse(body, 200, 'application/json') });
    renderApp('/partidos/1');
    expect(await screen.findByRole('heading', { name: 'Identificador numérico no representable' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Salida del modelo' })).not.toBeInTheDocument();
  });

  it('shows an unavailable state with retry when the backend is down', async () => {
    let calls = 0;
    mockFetch({
      '/competitions': competitions,
      '/fixtures/9': () => {
        calls += 1;
        return calls === 1 ? networkFailure() : jsonResponse(devFixture(9));
      },
    });
    const { user } = renderApp('/partidos/9');
    expect(await screen.findByRole('heading', { name: 'Servicio de datos no disponible' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Volver a los partidos/ })).toHaveAttribute('href', '/partidos');
    await user.click(screen.getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 9 vs DEV_ONLY Away 9' })).toBeInTheDocument();
  });

  // D1 (independent QA): a near-integer id token must never become another fixture's id.
  it.each([
    ['13', '12.9999999999999999'],
    ['42', '41.99999999999999999'],
    ['1', '1.0000000000000001'],
    ['13', '1.3e1'],
  ])('/partidos/%s refuses a response whose id token is %s (it would round to the requested id)', async (requested, token) => {
    const body = JSON.stringify(devFixture(Number(requested))).replace(`"id":${requested},`, `"id":${token},`);
    expect(body).toContain(`"id":${token},`);
    mockFetch({ '/competitions': competitions, [`/fixtures/${requested}`]: () => textResponse(body, 200, 'application/json') });
    const { router } = renderApp(`/partidos/${requested}`);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Identificador no válido en la respuesta');
    expect(alert).toHaveTextContent(`«${token}»`);
    expect(screen.queryByRole('heading', { level: 1, name: /vs/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Salida del modelo' })).not.toBeInTheDocument();
    expect(router.state.location.pathname).toBe(`/partidos/${requested}`);
  });

  it('refuses a well-formed response about a different fixture and never shows or links it', async () => {
    mockFetch({ '/competitions': competitions, '/fixtures/101': () => jsonResponse(devFinishedFixture(102)) });
    const { router } = renderApp('/partidos/101');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('La respuesta no corresponde al partido solicitado');
    expect(alert).toHaveTextContent('Se pidió el partido 101, pero el servicio de datos respondió con el partido 102.');
    expect(screen.queryByText(/DEV_ONLY Home 102/)).not.toBeInTheDocument();
    expect(document.querySelector('a[href="/partidos/102"]')).toBeNull();
    expect(router.state.location.pathname).toBe('/partidos/101');
  });

  it('a non-JSON 404 (proxy or static server) is an unavailable service, not a missing fixture (L1)', async () => {
    mockFetch({ '/competitions': competitions, '/fixtures/9': () => textResponse('<!doctype html><title>404</title>', 404, 'text/html') });
    renderApp('/partidos/9');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Servicio de datos no disponible');
    expect(alert).toHaveTextContent('HTTP 404');
    expect(screen.queryByRole('heading', { level: 1, name: 'Partido no encontrado' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
  });
});
