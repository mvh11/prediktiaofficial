import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider } from 'react-router';
import { ApiProvider } from '../api/ApiProvider.tsx';
import { createHttpClient } from '../api/httpClient.ts';
import { createPrediktiaApi, type PrediktiaApi } from '../api/prediktiaApi.ts';
import { routes } from '../app/routes.tsx';

export interface RenderAppOptions {
  /** Defaults to "/api" — i.e. a configured data source served through global fetch. */
  readonly baseUrl?: string | null;
  readonly timeoutMs?: number;
  readonly api?: PrediktiaApi;
}

export function createTestApi({ baseUrl = '/api', timeoutMs = 2_000 }: RenderAppOptions = {}): PrediktiaApi {
  return createPrediktiaApi(createHttpClient({ baseUrl, timeoutMs }));
}

/** Renders the real route tree in a memory router, starting at `path`. */
export function renderApp(path: string, options: RenderAppOptions = {}) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const api = options.api ?? createTestApi(options);
  const user = userEvent.setup();
  const utils = render(
    <ApiProvider api={api}>
      <RouterProvider router={router} />
    </ApiProvider>,
  );
  return { ...utils, router, user };
}
