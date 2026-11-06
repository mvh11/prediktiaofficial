import type { ReactNode } from 'react';
import { ApiContext } from './apiContext.ts';
import type { PrediktiaApi } from './prediktiaApi.ts';

export function ApiProvider({ api, children }: { api: PrediktiaApi; children: ReactNode }) {
  return <ApiContext value={api}>{children}</ApiContext>;
}
