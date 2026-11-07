import { describe, expect, it } from 'vitest';
import { createFormatters, utcDay } from './format.ts';
import { DEFAULT_I18N, DISPLAY_TIME_ZONE } from './i18n.ts';
import { esCL } from './messages.es-CL.ts';

const format = createFormatters('es-CL', DISPLAY_TIME_ZONE);

describe('es-CL formatters (America/Santiago)', () => {
  it('uses Chile as the display zone and es-CL as the locale', () => {
    expect(DEFAULT_I18N.locale).toBe('es-CL');
    expect(DEFAULT_I18N.timeZone).toBe('America/Santiago');
    expect(DEFAULT_I18N.messages).toBe(esCL);
  });

  it('converts the instant to Chile time, including daylight saving (summer UTC−3, winter UTC−4)', () => {
    // 15:00 UTC on 5 January (Chilean summer time) is 12:00 in Santiago.
    expect(format.time('2030-01-05T15:00:00Z')).toBe('12:00');
    expect(format.offset('2030-01-05T15:00:00Z')).toBe('UTC−3');
    // 15:00 UTC on 5 July (winter) is 11:00 in Santiago.
    expect(format.time('2030-07-05T15:00:00Z')).toBe('11:00');
    expect(format.offset('2030-07-05T15:00:00Z')).toBe('UTC−4');
  });

  it('groups by the Chilean calendar day, not the UTC day', () => {
    // 01:30 UTC on 6 January is still 5 January in Santiago.
    expect(format.dayKey('2030-01-06T01:30:00Z')).toBe('2030-01-05');
    expect(format.day('2030-01-06T01:30:00Z')).toMatch(/^sábado,? 5 de enero de 2030$/);
  });

  it('respects the offset carried by the timestamp', () => {
    expect(format.utc('2030-01-05T12:00:00-03:00')).toBe('2030-01-05 15:00 UTC');
    expect(format.time('2030-01-05T12:00:00-03:00')).toBe('12:00');
  });

  it('formats a calendar day without shifting it and integers with Chilean grouping', () => {
    expect(format.isoDay('2030-01-01')).toMatch(/1 .*ene.* 2030/);
    expect(format.integer(2_147_483_647)).toBe('2.147.483.647');
  });

  it('returns unparseable input unchanged instead of inventing a date', () => {
    expect(format.kickoff('not a date')).toBe('not a date');
  });

  it('computes UTC days with an offset', () => {
    const base = new Date('2030-01-31T23:30:00-03:00'); // 1 February 02:30 UTC
    expect(utcDay(base)).toBe('2030-02-01');
    expect(utcDay(base, -6)).toBe('2030-01-26');
  });
});
