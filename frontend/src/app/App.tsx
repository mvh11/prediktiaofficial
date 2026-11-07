import { useState } from 'react';
import { createBrowserRouter, RouterProvider } from 'react-router';
import { ApiProvider } from '../api/ApiProvider.tsx';
import { createDefaultApi, type PrediktiaApi } from '../api/prediktiaApi.ts';
import { I18nProvider } from '../i18n/I18nProvider.tsx';
import { routes } from './routes.tsx';

export interface AppProps {
  /** Injected in tests; production uses the configured default client. */
  readonly api?: PrediktiaApi;
}

export function App({ api }: AppProps) {
  const [router] = useState(() => createBrowserRouter(routes));
  const [defaultApi] = useState(() => api ?? createDefaultApi());
  return (
    <I18nProvider>
      <ApiProvider api={defaultApi}>
        <RouterProvider router={router} />
      </ApiProvider>
    </I18nProvider>
  );
}
