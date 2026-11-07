import { FILTER_PARAMS } from '../features/fixtures/fixtureFilters.ts';

/**
 * Phase 1 used English paths (/fixtures, /research, /methodology) and English
 * query parameters. Old bookmarks and shared links keep working: they are
 * translated once, with `replace`, to the canonical Spanish URL.
 */
const LEGACY_PARAMS: Readonly<Record<string, string>> = {
  competition: FILTER_PARAMS.competition,
  season: FILTER_PARAMS.season,
  team: FILTER_PARAMS.team,
  from: FILTER_PARAMS.from,
  to: FILTER_PARAMS.to,
  status: FILTER_PARAMS.status,
  page: FILTER_PARAMS.page,
};

const LEGACY_STATUS: Readonly<Record<string, string>> = { finished: 'finalizados', pending: 'pendientes' };

export const LEGACY_ANCHORS: Readonly<Record<string, string>> = {
  '#provenance': '#procedencia',
  '#point-in-time': '#punto-en-el-tiempo',
  '#validation': '#validacion',
  '#calibration': '#calibracion',
  '#research-only': '#solo-investigacion',
  '#evidence': '#requisitos',
  '#abstain': '#abstencion',
};

export function translateLegacySearch(search: string): string {
  const params = new URLSearchParams(search);
  const next = new URLSearchParams();
  for (const [key, value] of params) {
    const name = LEGACY_PARAMS[key] ?? key;
    next.append(name, name === FILTER_PARAMS.status ? (LEGACY_STATUS[value] ?? value) : value);
  }
  const text = next.toString();
  return text ? `?${text}` : '';
}
