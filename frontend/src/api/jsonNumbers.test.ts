import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import {
  findIdentifierIssue,
  findIntegerTokenIssue,
  findUnsafeIntegerLiteral,
  IDENTIFIER_RULES,
  INTEGER_FIELD_RULES,
  isValidIdentifierToken,
  isValidIntegerFieldToken,
} from './jsonNumbers.ts';
import { competitionSchema, fixtureSchema, teamSchema } from './schemas.ts';

describe('findUnsafeIntegerLiteral', () => {
  it('accepts safe integers, including the largest one', () => {
    expect(findUnsafeIntegerLiteral('[1,2147483647,-5,9007199254740991,-9007199254740991]')).toBeNull();
  });

  it.each([
    ['{"id":9007199254740992}', '9007199254740992'],
    ['{"id":9007199254740993}', '9007199254740993'],
    ['[1, -9007199254740993]', '-9007199254740993'],
    ['{"a":{"b":[0,123456789012345678901234567890]}}', '123456789012345678901234567890'],
  ])('reports %s', (text, literal) => {
    expect(findUnsafeIntegerLiteral(text)).toBe(literal);
  });

  it('ignores digits inside strings, including escaped quotes', () => {
    expect(findUnsafeIntegerLiteral('{"name":"9007199254740993","note":"say \\"12345678901234567890\\""}')).toBeNull();
  });

  it('leaves fractional and exponent numbers to the identifier and schema checks', () => {
    expect(findUnsafeIntegerLiteral('[1.5, 12345678901234567890.5, 1e400]')).toBeNull();
  });
});

describe('identifier tokens (D1)', () => {
  it.each([
    // the three counterexamples of the independent QA, then exponents and other non-canonical spellings
    '12.9999999999999999',
    '41.99999999999999999',
    '1.0000000000000001',
    '1.3e1',
    '1E0',
    '1e+0',
    '2.0',
    '0',
    '-0',
    '-1',
    '2147483648',
    '9007199254740993',
  ])('rejects the id token %s', (token) => {
    expect(isValidIdentifierToken('id', token)).toBe(false);
    expect(isValidIdentifierToken('competition_id', token)).toBe(false);
  });

  it.each(['1', '42', '2000000000', '2147483647'])('accepts the canonical in-range id %s', (token) => {
    expect(isValidIdentifierToken('id', token)).toBe(true);
  });

  it('external ids may be zero or negative within int32, but never non-canonical', () => {
    for (const ok of ['0', '-1', '-2147483648', '2147483647', '700001']) expect(isValidIdentifierToken('external_id', ok)).toBe(true);
    for (const bad of ['-0', '-2147483649', '2147483648', '700001.0', '7e5', '01']) expect(isValidIdentifierToken('external_id', bad)).toBe(false);
  });

  it('does not constrain numbers of other fields', () => {
    expect(isValidIdentifierToken('home_goals', '1.5')).toBe(true);
    expect(findIdentifierIssue('{"elapsed":1e2,"season":2030.5,"probability":0.25}')).toBeNull();
  });

  it('finds an offending identifier at any depth and reports the 1-based record of a list', () => {
    expect(findIdentifierIssue('{"id":12.9999999999999999}')).toEqual({ literal: '12.9999999999999999', key: 'id' });
    expect(findIdentifierIssue('[{"id":1,"home_team":{"id":2}},{"id":3,"away_team":{"id":4.0000000000000001}}]')).toEqual({
      literal: '4.0000000000000001',
      key: 'id',
      row: 2,
    });
    expect(findIdentifierIssue('[{"id":1},{"id":2},{"competition_id":1e1}]')).toEqual({ literal: '1e1', key: 'competition_id', row: 3 });
  });

  it('decodes escaped keys exactly as JSON.parse does and checks duplicate keys', () => {
    expect(findIdentifierIssue('{"i\\u0064":12.9999999999999999}')).toEqual({ literal: '12.9999999999999999', key: 'id' });
    expect(findIdentifierIssue('{"id":5,"id":1.0000000000000001}')).toEqual({ literal: '1.0000000000000001', key: 'id' });
    expect(findIdentifierIssue('{"identity":1.5,"ids":[1.5],"id_x":2.5}')).toBeNull();
  });

  it('ignores strings that contain JSON-looking text, including escaped quotes and braces', () => {
    const text = JSON.stringify({ id: 7, venue_name: '{"id":12.9999999999999999}', referee: 'say "id": 1e3 ]} {[', city: '\\"id\\":0' });
    expect(findIdentifierIssue(text)).toBeNull();
  });

  it('accepts real backend output unchanged', () => {
    const text = JSON.stringify([{ id: 1, external_id: 9001, competition_id: 2, home_team: { id: 10 }, away_team: { id: 2147483647 } }]);
    expect(findIdentifierIssue(text)).toBeNull();
  });
});

describe('contract integer tokens (FE2-R1)', () => {
  const INTEGER_FIELDS = Object.keys(INTEGER_FIELD_RULES);

  it.each([
    // the examples of the order, then the same losses on other fields
    ['season', '2029.9999999999999999', 2030],
    ['home_goals', '0.99999999999999999', 1],
    ['elapsed', '6.7e1', 67],
    ['away_goals', '1.0000000000000001', 1],
    ['current_season', '2.03e3', 2030],
    ['founded', '1899.99999999999999999', 1900],
    ['fulltime_home', '2E0', 2],
  ])('rejects %s token %s, which JSON.parse would silently read as %d', (key, token, rounded) => {
    const text = `{"${key}":${token}}`;
    expect(JSON.parse(text)).toEqual({ [key]: rounded }); // the loss being guarded against
    expect(findIntegerTokenIssue(text)).toEqual({ literal: token, key, kind: 'integer' });
  });

  it.each(['1.0', '0.0', '1e0', '1E+0', '1.5', '-0', '01', '00', '2147483648', '-2147483649', '1.0000000000000001'])(
    'rejects the non-canonical or out-of-range token %s in every contract integer field',
    (token) => {
      for (const key of INTEGER_FIELDS) {
        expect(isValidIntegerFieldToken(key, token), `${key}: ${token}`).toBe(false);
        expect(findIntegerTokenIssue(`{"${key}":${token}}`), `${key}: ${token}`).toMatchObject({ key, literal: token, kind: 'integer' });
      }
    },
  );

  it.each(['0', '1', '2030', '-1', '2147483647', '-2147483648'])('accepts the canonical int32 token %s (domain limits stay in the schemas)', (token) => {
    for (const key of INTEGER_FIELDS) expect(isValidIntegerFieldToken(key, token), key).toBe(true);
  });

  it('accepts null in every optional integer field', () => {
    const text = JSON.stringify(Object.fromEntries(INTEGER_FIELDS.map((key) => [key, null])));
    expect(findIntegerTokenIssue(text)).toBeNull();
  });

  it('leaves legitimately decimal numbers of non-contract fields alone', () => {
    expect(findIntegerTokenIssue('{"id":1,"probability":0.25,"ratio":1.5e-3,"score":2.0,"items":[1.5,2e3]}')).toBeNull();
  });

  it('keeps the identifier rules exactly as before and reports them as identifiers', () => {
    for (const token of ['12.9999999999999999', '41.99999999999999999', '1.0000000000000001', '1.3e1', '0', '-1', '2147483648']) {
      expect(findIntegerTokenIssue(`{"id":${token}}`)).toEqual({ literal: token, key: 'id', kind: 'identifier' });
      expect(findIntegerTokenIssue(`{"competition_id":${token}}`)).toEqual({ literal: token, key: 'competition_id', kind: 'identifier' });
    }
    expect(findIntegerTokenIssue('{"external_id":-0}')).toEqual({ literal: '-0', key: 'external_id', kind: 'identifier' });
    expect(findIntegerTokenIssue('{"external_id":-5,"id":2147483647}')).toBeNull();
  });

  it('reports the first offending token in text order, at any depth, with the 1-based record of a list', () => {
    const text =
      '[{"id":1,"season":2030,"home_team":{"id":2}},{"id":3,"season":2030,"home_goals":0.99999999999999999,"away_team":{"id":4.0000000000000001}}]';
    expect(findIntegerTokenIssue(text)).toEqual({ literal: '0.99999999999999999', key: 'home_goals', kind: 'integer', row: 2 });
    expect(findIntegerTokenIssue('{"home_team":{"id":1},"elapsed":6.7e1}')).toEqual({ literal: '6.7e1', key: 'elapsed', kind: 'integer' });
  });

  it('decodes escaped keys and checks every duplicate key', () => {
    expect(findIntegerTokenIssue('{"se\u0061son":2029.9999999999999999}')).toEqual({ literal: '2029.9999999999999999', key: 'season', kind: 'integer' });
    expect(findIntegerTokenIssue('{"home_goals":1,"home_goals":0.99999999999999999}')).toEqual({
      literal: '0.99999999999999999',
      key: 'home_goals',
      kind: 'integer',
    });
    expect(findIntegerTokenIssue('{"seasons":2030.5,"season_note":6.7e1,"elapsed_ms":1.5}')).toBeNull();
  });

  it('never treats JSON-looking text inside strings as numbers', () => {
    const text = JSON.stringify({
      season: 2030,
      venue_name: '{"season":2029.9999999999999999}',
      referee: 'say "home_goals": 0.99999999999999999 ]} {[',
      round: '\\"elapsed\\":6.7e1',
    });
    expect(findIntegerTokenIssue(text)).toBeNull();
  });

  it('never resolves a response key to a prototype member', () => {
    const text = '{"__proto__":1.5,"constructor":2.5,"toString":1e3,"hasOwnProperty":0.99999999999999999}';
    expect(() => findIntegerTokenIssue(text)).not.toThrow();
    expect(findIntegerTokenIssue(text)).toBeNull();
    expect(findIdentifierIssue(text)).toBeNull();
  });

  it('covers exactly the integer fields of the verified response schemas, and no schema field is a decimal', () => {
    const integerFields = new Set<string>();
    const decimalFields: string[] = [];
    const visit = (shape: Record<string, z.ZodType>) => {
      for (const [key, field] of Object.entries(shape)) {
        const inner = field instanceof z.ZodNullable ? field.unwrap() : field;
        if (inner instanceof z.ZodObject) visit(inner.shape);
        else if (inner instanceof z.ZodNumber) {
          if (inner.format === 'safeint') integerFields.add(key);
          else decimalFields.push(key);
        }
      }
    };
    for (const schema of [competitionSchema, teamSchema, fixtureSchema]) visit(schema.shape);
    expect(decimalFields).toEqual([]);
    expect([...integerFields].sort()).toEqual([...Object.keys(IDENTIFIER_RULES), ...INTEGER_FIELDS].sort());
  });
});
