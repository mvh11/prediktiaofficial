import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { ApiProvider } from '../api/ApiProvider.tsx';
import { createHttpClient } from '../api/httpClient.ts';
import { createPrediktiaApi, type PrediktiaApi } from '../api/prediktiaApi.ts';
import { pageLoaders } from '../app/lazyPages.ts';
import { routes } from '../app/routes.tsx';
import { I18nProvider } from '../i18n/I18nProvider.tsx';

// W2: a page's first use in a test file includes Vite's on-demand transform of the page and its imports, which under
// CPU load took longer than a findBy* budget (the failure DOM still showed "Cargando página…"). Warming the page modules
// once per test file, before any test runs, keeps those waits about UI behaviour. React.lazy still resolves through
// Suspense; cold loading itself is covered by src/app/lazyPages.test.tsx, which does not import this module.
await Promise.all(Object.values(pageLoaders).map((load) => load()));

export interface RenderAppOptions {
  /** Defaults to "/api" — i.e. a configured data source served through global fetch. */
  readonly baseUrl?: string | null;
  /**
   * Client timeout. The default is far above any mocked response time so that CPU starvation can never turn a
   * success scenario into a timeout; tests of the timeout state pass a small value explicitly.
   */
  readonly timeoutMs?: number;
  readonly api?: PrediktiaApi;
}

export function createTestApi({ baseUrl = '/api', timeoutMs = 30_000 }: RenderAppOptions = {}): PrediktiaApi {
  return createPrediktiaApi(createHttpClient({ baseUrl, timeoutMs }));
}

/** Renders the real route tree in a memory router, starting at `path`. */
export function renderApp(path: string, options: RenderAppOptions = {}) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const api = options.api ?? createTestApi(options);
  const user = userEvent.setup();
  const utils = render(
    <I18nProvider>
      <ApiProvider api={api}>
        <RouterProvider router={router} />
      </ApiProvider>
    </I18nProvider>,
  );
  return { ...utils, router, user };
}
