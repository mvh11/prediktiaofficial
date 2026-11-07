import { screen } from '@testing-library/react';
import axe from 'axe-core';
import { describe, expect, it } from 'vitest';
import { devCompetition, devFinishedFixture, devFixture, devTeam } from '../test/devOnlyData.ts';
import { jsonResponse, mockFetch, networkFailure } from '../test/fetchMock.ts';
import { renderApp } from '../test/renderApp.tsx';

/**
 * Automated WCAG checks with axe-core on fully rendered pages. Colour contrast
 * needs real layout and is checked in the browser by the e2e suite instead.
 */
async function expectNoAxeViolations() {
  const results = await axe.run(document, {
    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] },
    rules: { 'color-contrast': { enabled: false } },
  });
  const summary = results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`);
  expect(summary).toEqual([]);
}

const health = () => jsonResponse({ status: 'ok', service: 'prediktia-backend' });
const dataRoutes = {
  '/health': health,
  '/competitions': () => jsonResponse([devCompetition(), devCompetition({ id: 2, name: 'DEV_ONLY Cup B', country: null, type: 'Cup' })]),
  '/competitions/1/teams': () => jsonResponse([devTeam(11), devTeam(12, { is_national: true, founded: 1900 })]),
  '/fixtures': () => jsonResponse([devFixture(1), devFinishedFixture(2), devFixture(3, { status_short: '2H', home_goals: 0, away_goals: 0 })]),
  '/fixtures/2': () => jsonResponse(devFinishedFixture(2)),
};

describe('accessibility (axe-core)', () => {
  it.each([
    ['/', /basadas en evidencia/i],
    ['/investigacion', 'Enfoque de investigación cuantitativa'],
    ['/metodologia', 'Metodología y evidencia'],
    ['/nada', 'Página no encontrada'],
    ['/partidos/abc', 'Identificador no válido'],
    ['/competiciones/99999999999999999999', 'Identificador fuera del rango admitido'],
  ])('%s has no violations', async (path, heading) => {
    renderApp(path, { baseUrl: null });
    await screen.findByRole('heading', { level: 1, name: heading });
    await expectNoAxeViolations();
  });

  it('competition catalog has no violations', async () => {
    mockFetch(dataRoutes);
    renderApp('/competiciones');
    await screen.findByRole('link', { name: 'DEV_ONLY League A' });
    await screen.findByText('Conectado');
    await expectNoAxeViolations();
  });

  it('competition detail has no violations', async () => {
    mockFetch(dataRoutes);
    renderApp('/competiciones/1');
    await screen.findByRole('list', { name: 'Equipos' });
    await screen.findByRole('link', { name: /DEV_ONLY Home 1/ });
    await expectNoAxeViolations();
  });

  it('fixture browser with data, chips and pagination has no violations', async () => {
    mockFetch({ ...dataRoutes, '/fixtures': () => jsonResponse(Array.from({ length: 26 }, (_, index) => devFixture(index + 1))) });
    renderApp('/partidos?competicion=1&equipo=11&estado=pendientes&pagina=1');
    await screen.findByRole('navigation', { name: 'Paginación' });
    await screen.findByText('Conectado');
    await expectNoAxeViolations();
  });

  it('fixture browser in the unavailable state has no violations', async () => {
    mockFetch({ '/health': networkFailure, '/competitions': networkFailure, '/fixtures': networkFailure });
    renderApp('/partidos');
    await screen.findByRole('heading', { name: 'Servicio de datos no disponible' });
    await expectNoAxeViolations();
  });

  it('fixture detail has no violations', async () => {
    mockFetch(dataRoutes);
    renderApp('/partidos/2');
    await screen.findByRole('region', { name: 'Salida del modelo' });
    await expectNoAxeViolations();
  });
});
