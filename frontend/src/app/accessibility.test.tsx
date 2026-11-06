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

describe('accessibility (axe-core)', () => {
  it.each([
    ['/', /built on evidence/i],
    ['/research', 'Quantitative research approach'],
    ['/methodology', 'Methodology and evidence'],
    ['/nope', 'Page not found'],
  ])('%s has no violations', async (path, heading) => {
    renderApp(path);
    await screen.findByRole('heading', { level: 1, name: heading });
    await expectNoAxeViolations();
  });

  it('fixture browser with data has no violations', async () => {
    mockFetch({
      '/health': () => jsonResponse({ status: 'ok', service: 'prediktia-backend' }),
      '/competitions': () => jsonResponse([devCompetition()]),
      '/competitions/1/teams': () => jsonResponse([devTeam(11)]),
      '/fixtures': () => jsonResponse([devFixture(1), devFinishedFixture(2)]),
    });
    renderApp('/fixtures?competition=1');
    await screen.findByRole('list', { name: 'Fixtures' });
    await screen.findByText('Connected');
    await expectNoAxeViolations();
  });

  it('fixture browser in the unavailable state has no violations', async () => {
    mockFetch({ '/health': networkFailure, '/competitions': networkFailure });
    renderApp('/fixtures');
    await screen.findByRole('heading', { name: 'Data service unavailable' });
    await expectNoAxeViolations();
  });

  it('fixture detail has no violations', async () => {
    mockFetch({ '/fixtures/2': () => jsonResponse(devFinishedFixture(2)) });
    renderApp('/fixtures/2');
    await screen.findByRole('region', { name: 'Model output' });
    await expectNoAxeViolations();
  });
});
