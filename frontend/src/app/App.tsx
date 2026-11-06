import { useState } from 'react';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { ApiProvider } from '../api/ApiProvider.tsx';
import { createDefaultApi, type PrediktiaApi } from '../api/prediktiaApi.ts';
import { routes } from './routes.tsx';

export interface AppProps {
  /** Injected in tests; production uses the configured default client. */
  readonly api?: PrediktiaApi;
}

export function App({ api }: AppProps) {
  const [router] = useState(() => createBrowserRouter(routes));
  const [defaultApi] = useState(() => api ?? createDefaultApi());
  return (
    <ApiProvider api={defaultApi}>
      <RouterProvider router={router} />
    </ApiProvider>
  );
}
