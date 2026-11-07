import { describe, expect, it } from 'vitest';
import { findIdentifierIssue, findUnsafeIntegerLiteral, isValidIdentifierToken } from './jsonNumbers.ts';

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
