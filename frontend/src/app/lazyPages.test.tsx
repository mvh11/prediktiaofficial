import { render, screen, within } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { describe, expect, it } from 'vitest';
import { ApiProvider } from '../api/ApiProvider.tsx';
import { createHttpClient } from '../api/httpClient.ts';
import { createPrediktiaApi } from '../api/prediktiaApi.ts';
import { I18nProvider } from '../i18n/I18nProvider.tsx';
import { devCompetition, devFinishedFixture } from '../test/devOnlyData.ts';
import { jsonResponse, mockFetch } from '../test/fetchMock.ts';
import { pageLoaders } from './lazyPages.ts';
import { routes } from './routes.tsx';

/*
 * Cold lazy loading (W2). This file deliberately does NOT import src/test/renderApp.tsx, which warms every page module,
 * so each route below is the first use of its page chunk in this file. These tests check that cold loading is correct
 * (fallback first, then the page with its title), not how fast Vite transforms a module on a loaded machine; hence the
 * explicit, generous budget on the waits that include a cold import.
 */
const COLD_IMPORT_BUDGET = { timeout: 20_000 };

function renderCold(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const api = createPrediktiaApi(createHttpClient({ baseUrl: '/api', timeoutMs: 30_000 }));
  render(
    <I18nProvider>
      <ApiProvider api={api}>
        <RouterProvider router={router} />
      </ApiProvider>
    </I18nProvider>,
  );
  return router;
}

// Tests run in declaration order; the loader check comes last because it imports (warms) every page module.
describe('code-split pages', () => {
  it('a cold static page shows the announced page fallback, then the page with its title already set', async () => {
    renderCold('/metodologia');
    expect(within(screen.getByRole('main')).getByRole('status')).toHaveTextContent('Cargando página…');
    const heading = await screen.findByRole('heading', { level: 1, name: 'Metodología y evidencia' }, COLD_IMPORT_BUDGET);
    expect(document.title).toBe('Metodología · PREDIKTIA');
    expect(screen.queryByText('Cargando página…')).not.toBeInTheDocument();
    expect(heading).toBeVisible();
  });

  it('a cold data page shows the page fallback first, then the stored fixture with its title and ABSTAIN', async () => {
    mockFetch({ '/competitions': () => jsonResponse([devCompetition()]), '/fixtures/7': () => jsonResponse(devFinishedFixture(7)) });
    renderCold('/partidos/7');
    expect(within(screen.getByRole('main')).getByRole('status')).toHaveTextContent('Cargando página…');
    await screen.findByRole('heading', { level: 1, name: 'DEV_ONLY Home 7 vs DEV_ONLY Away 7' }, COLD_IMPORT_BUDGET);
    expect(document.title).toBe('DEV_ONLY Home 7 vs DEV_ONLY Away 7 · PREDIKTIA');
    expect(screen.getByRole('region', { name: 'Salida del modelo' })).toHaveTextContent('ABSTAIN');
    expect(screen.queryByText('Cargando página…')).not.toBeInTheDocument();
  });

  it.each(Object.entries(pageLoaders))('%s is a lazily loaded module that provides that component', async (name, load) => {
    const module: Record<string, unknown> = await load();
    expect(typeof module[name]).toBe('function');
  });
});
