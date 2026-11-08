import { lazy } from 'react';

// Code-split pages. The landing page ships in the main bundle (see routes.tsx).
const loadCompetitionsPage = () => import('../features/competitions/CompetitionsPage.tsx');
const loadCompetitionDetailPage = () => import('../features/competitions/CompetitionDetailPage.tsx');
const loadFixturesPage = () => import('../features/fixtures/FixturesPage.tsx');
const loadFixtureDetailPage = () => import('../features/fixtures/FixtureDetailPage.tsx');
const loadResearchPage = () => import('../pages/ResearchPage.tsx');
const loadMethodologyPage = () => import('../pages/MethodologyPage.tsx');

export const CompetitionsPage = lazy(() => loadCompetitionsPage().then((m) => ({ default: m.CompetitionsPage })));
export const CompetitionDetailPage = lazy(() => loadCompetitionDetailPage().then((m) => ({ default: m.CompetitionDetailPage })));
export const FixturesPage = lazy(() => loadFixturesPage().then((m) => ({ default: m.FixturesPage })));
export const FixtureDetailPage = lazy(() => loadFixtureDetailPage().then((m) => ({ default: m.FixtureDetailPage })));
export const ResearchPage = lazy(() => loadResearchPage().then((m) => ({ default: m.ResearchPage })));
export const MethodologyPage = lazy(() => loadMethodologyPage().then((m) => ({ default: m.MethodologyPage })));

/** Every code-split page module, keyed by the component it provides (used by the test harness to warm modules). */
export const pageLoaders = {
  CompetitionsPage: loadCompetitionsPage,
  CompetitionDetailPage: loadCompetitionDetailPage,
  FixturesPage: loadFixturesPage,
  FixtureDetailPage: loadFixtureDetailPage,
  ResearchPage: loadResearchPage,
  MethodologyPage: loadMethodologyPage,
} as const;
