import type { RouteObject } from 'react-router';
import { AppLayout } from '../components/layout/AppLayout.tsx';
import { HomePage } from '../pages/HomePage.tsx';
import { NotFoundPage } from '../pages/NotFoundPage.tsx';
import { RouteErrorPage } from '../pages/RouteErrorPage.tsx';
import { FixtureDetailPage, FixturesPage, MethodologyPage, ResearchPage } from './lazyPages.ts';

export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'fixtures', element: <FixturesPage /> },
      { path: 'fixtures/:fixtureId', element: <FixtureDetailPage /> },
      { path: 'research', element: <ResearchPage /> },
      { path: 'methodology', element: <MethodologyPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
];
