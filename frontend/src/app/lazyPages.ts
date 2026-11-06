import { lazy } from 'react';

// Code-split pages. The landing page ships in the main bundle (see routes.tsx).
export const FixturesPage = lazy(() => import('../features/fixtures/FixturesPage.tsx').then((m) => ({ default: m.FixturesPage })));
export const FixtureDetailPage = lazy(() =>
  import('../features/fixtures/FixtureDetailPage.tsx').then((m) => ({ default: m.FixtureDetailPage })),
);
export const ResearchPage = lazy(() => import('../pages/ResearchPage.tsx').then((m) => ({ default: m.ResearchPage })));
export const MethodologyPage = lazy(() => import('../pages/MethodologyPage.tsx').then((m) => ({ default: m.MethodologyPage })));
