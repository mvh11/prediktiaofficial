import { Navigate, useLocation, useParams } from 'react-router';
import { PATHS } from '../components/layout/navigation.ts';
import { LEGACY_ANCHORS, translateLegacySearch } from './legacyUrls.ts';

/** Phase 1 (English) URLs are translated once, with `replace`, to the canonical Spanish URL. */
export function LegacyFixturesRedirect() {
  const { search } = useLocation();
  return <Navigate to={`${PATHS.fixtures}${translateLegacySearch(search)}`} replace />;
}

export function LegacyFixtureRedirect() {
  const { fixtureId = '' } = useParams();
  return <Navigate to={`${PATHS.fixtures}/${encodeURIComponent(fixtureId)}`} replace />;
}

export function LegacyPageRedirect({ to }: { to: string }) {
  const { hash } = useLocation();
  return <Navigate to={`${to}${LEGACY_ANCHORS[hash] ?? hash}`} replace />;
}
