import { describe, expect, it } from 'vitest';
import { devFinishedFixture, devFixture } from '../test/devOnlyData.ts';
import { fixtureSchema } from './schemas.ts';

describe('fixtureSchema', () => {
  it('accepts the verified FixtureOut shape', () => {
    expect(fixtureSchema.safeParse(devFixture(1)).success).toBe(true);
    expect(fixtureSchema.safeParse(devFinishedFixture(2)).success).toBe(true);
  });

  it.each(['2030-01-05T15:00:00Z', '2030-01-05T15:00:00.123456+00:00', '2030-01-05T15:00-03:00'])('accepts zoned timestamp %s', (kickoff) => {
    expect(fixtureSchema.safeParse(devFixture(1, { kickoff_at: kickoff })).success).toBe(true);
  });

  it.each(['2030-01-05T15:00:00', '2030-01-05', 'not a date', '2030-13-45T99:99:00Z'])('rejects ambiguous or invalid timestamp %s', (kickoff) => {
    expect(fixtureSchema.safeParse(devFixture(1, { kickoff_at: kickoff })).success).toBe(false);
  });

  it('rejects negative or fractional goals', () => {
    expect(fixtureSchema.safeParse(devFixture(1, { home_goals: -1 })).success).toBe(false);
    expect(fixtureSchema.safeParse(devFixture(1, { home_goals: 1.5 })).success).toBe(false);
  });

  it('rejects a response missing required fields', () => {
    const { is_finished: _omitted, ...partial } = devFixture(1);
    expect(fixtureSchema.safeParse(partial).success).toBe(false);
  });
});
