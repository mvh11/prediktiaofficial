import type { Fixture } from '../../api/schemas.ts';
import type { Messages } from '../../i18n/messages.es-CL.ts';

/**
 * Status codes stored by the backend are API-Football short codes
 * (see backend/app/models/fixture.py). The backend itself defines
 * FINISHED_STATUSES = {FT, AET, PEN} and FINAL_STATUSES = FINISHED + {AWD, WO}.
 */
export type FixturePhase = 'scheduled' | 'in_play' | 'finished' | 'awarded' | 'postponed' | 'interrupted' | 'cancelled' | 'unknown';

const PHASE_BY_CODE: Readonly<Record<string, FixturePhase>> = {
  TBD: 'scheduled',
  NS: 'scheduled',
  '1H': 'in_play',
  HT: 'in_play',
  '2H': 'in_play',
  ET: 'in_play',
  BT: 'in_play',
  P: 'in_play',
  LIVE: 'in_play',
  FT: 'finished',
  AET: 'finished',
  PEN: 'finished',
  AWD: 'awarded',
  WO: 'awarded',
  PST: 'postponed',
  SUSP: 'interrupted',
  INT: 'interrupted',
  CANC: 'cancelled',
  ABD: 'cancelled',
};

export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger';

const TONE_BY_PHASE: Readonly<Record<FixturePhase, StatusTone>> = {
  scheduled: 'info',
  in_play: 'warning',
  finished: 'success',
  awarded: 'neutral',
  postponed: 'warning',
  interrupted: 'warning',
  cancelled: 'danger',
  unknown: 'neutral',
};

export function fixturePhase(statusShort: string): FixturePhase {
  return PHASE_BY_CODE[statusShort.toUpperCase()] ?? 'unknown';
}

/** Localized label; unknown codes fall back to the provider's long text, then the raw code. */
export function fixtureStatusLabel(fixture: Pick<Fixture, 'status_short' | 'status_long'>, m: Messages): string {
  return m.status[fixture.status_short.toUpperCase()] ?? fixture.status_long ?? fixture.status_short;
}

export function phaseTone(phase: FixturePhase): StatusTone {
  return TONE_BY_PHASE[phase];
}

export type ScoreKind = 'final' | 'lastSync' | 'fulltime' | 'halftime' | 'extratime' | 'penalties';

export interface ScoreLine {
  readonly kind: ScoreKind;
  readonly home: number;
  readonly away: number;
}

/**
 * Whether the stored score is a final result: only FT/AET/PEN (finished) and
 * AWD/WO (administrative result), matching the backend's FINAL_STATUSES.
 */
export function hasFinalScore(fixture: Pick<Fixture, 'status_short'>): boolean {
  const phase = fixturePhase(fixture.status_short);
  return phase === 'finished' || phase === 'awarded';
}

/**
 * Score lines exactly as stored. Only complete pairs are shown (the backend
 * stores scores as atomic pairs). No score is ever derived or inferred, and the
 * main pair is only a "final" result when the fixture actually ended; for
 * in-play, suspended, abandoned or postponed fixtures it is the score recorded
 * at the last synchronization.
 */
export function scoreLines(fixture: Fixture): ScoreLine[] {
  const lines: ScoreLine[] = [];
  const push = (kind: ScoreKind, home: number | null, away: number | null) => {
    if (home !== null && away !== null) lines.push({ kind, home, away });
  };
  push(hasFinalScore(fixture) ? 'final' : 'lastSync', fixture.home_goals, fixture.away_goals);
  push('fulltime', fixture.fulltime_home, fixture.fulltime_away);
  push('halftime', fixture.halftime_home, fixture.halftime_away);
  push('extratime', fixture.extratime_home, fixture.extratime_away);
  push('penalties', fixture.penalty_home, fixture.penalty_away);
  return lines;
}

/** Primary score shown in lists: only for final statuses with a complete pair. */
export function headlineScore(fixture: Fixture): ScoreLine | null {
  if (!hasFinalScore(fixture)) return null;
  if (fixture.home_goals === null || fixture.away_goals === null) return null;
  return { kind: 'final', home: fixture.home_goals, away: fixture.away_goals };
}
