import type { Competition } from '../../api/schemas.ts';
import type { Messages } from '../../i18n/messages.es-CL.ts';
import { normalizeForSearch } from '../../lib/format.ts';

/** Provider type ("League"/"Cup") in the interface language; other values are shown as stored. */
export function competitionType(type: string | null, m: Messages): string | null {
  if (type === null) return null;
  if (type.toLowerCase() === 'league') return m.competitions.typeLeague;
  if (type.toLowerCase() === 'cup') return m.competitions.typeCup;
  return type;
}

/** "Name (Country)" — country names are provider data and are shown as stored. */
export function competitionLabel(competition: Competition): string {
  return competition.country ? `${competition.name} (${competition.country})` : competition.name;
}

export interface CountryGroup {
  readonly country: string;
  readonly competitions: readonly Competition[];
}

/** Keeps backend order (country, then name) and groups consecutive competitions by country. */
export function groupByCountry(competitions: readonly Competition[], m: Messages): CountryGroup[] {
  const groups: { country: string; competitions: Competition[] }[] = [];
  for (const competition of competitions) {
    const country = competition.country ?? m.competitions.noCountry;
    const existing = groups.find((group) => group.country === country);
    if (existing) existing.competitions.push(competition);
    else groups.push({ country, competitions: [competition] });
  }
  return groups;
}

export function matchesCompetition(competition: Competition, query: string): boolean {
  const needle = normalizeForSearch(query);
  if (needle === '') return true;
  return normalizeForSearch(`${competition.name} ${competition.country ?? ''}`).includes(needle);
}
