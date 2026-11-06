import { createContext, use } from 'react';
import type { PrediktiaApi } from './prediktiaApi.ts';

export const ApiContext = createContext<PrediktiaApi | null>(null);

export function useApi(): PrediktiaApi {
  const api = use(ApiContext);
  if (api === null) throw new Error('useApi must be used inside <ApiProvider>.');
  return api;
}
