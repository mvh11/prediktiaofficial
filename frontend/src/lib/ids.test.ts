import { describe, expect, it } from 'vitest';
import { MAX_DB_INT, parseId, parsePositiveInt } from './ids.ts';

describe('parseId', () => {
  it.each(['1', '42', '2000000000', String(MAX_DB_INT)])('accepts the canonical in-range id %s', (raw) => {
    expect(parseId(raw)).toEqual({ kind: 'valid', value: Number(raw), canonical: raw });
  });

  it.each(['2147483648', '9999999999', '12345678901', '9007199254740993', '99999999999999999999999999'])(
    'reports %s as unsupported without converting it',
    (raw) => {
      expect(parseId(raw)).toEqual({ kind: 'unsupported', raw });
    },
  );

  it.each(['', '0', '007', '-1', '1.0', '1e3', ' 1', 'abc', '١٢'])('reports %j as invalid', (raw) => {
    expect(parseId(raw)).toEqual({ kind: 'invalid', raw });
  });

  it('treats a missing value as invalid', () => {
    expect(parseId(undefined)).toEqual({ kind: 'invalid', raw: '' });
    expect(parseId(null)).toEqual({ kind: 'invalid', raw: '' });
  });

  it('parsePositiveInt returns only in-range values', () => {
    expect(parsePositiveInt('42')).toBe(42);
    expect(parsePositiveInt('2147483648')).toBeUndefined();
    expect(parsePositiveInt('0')).toBeUndefined();
  });
});
