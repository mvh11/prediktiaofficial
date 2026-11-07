import type { RouteObject } from 'react-router';
import { AppLayout } from '../components/layout/AppLayout.tsx';
import { PATHS } from '../components/layout/navigation.ts';
import { HomePage } from '../pages/HomePage.tsx';
import { NotFoundPage } from '../pages/NotFoundPage.tsx';
import { RouteErrorPage } from '../pages/RouteErrorPage.tsx';
import { LegacyFixtureRedirect, LegacyFixturesRedirect, LegacyPageRedirect } from './legacyRedirects.tsx';
import { CompetitionDetailPage, CompetitionsPage, FixtureDetailPage, FixturesPage, MethodologyPage, ResearchPage } from './lazyPages.ts';

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'competiciones', element: <CompetitionsPage /> },
      { path: 'competiciones/:competitionId', element: <CompetitionDetailPage /> },
      { path: 'partidos', element: <FixturesPage /> },
      { path: 'partidos/:fixtureId', element: <FixtureDetailPage /> },
      { path: 'investigacion', element: <ResearchPage /> },
      { path: 'metodologia', element: <MethodologyPage /> },
      // Phase 1 (English) URLs.
      { path: 'fixtures', element: <LegacyFixturesRedirect /> },
      { path: 'fixtures/:fixtureId', element: <LegacyFixtureRedirect /> },
      { path: 'research', element: <LegacyPageRedirect to={PATHS.research} /> },
      { path: 'methodology', element: <LegacyPageRedirect to={PATHS.methodology} /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
