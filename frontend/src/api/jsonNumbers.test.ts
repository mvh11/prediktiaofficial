import { describe, expect, it } from 'vitest';
import { findUnsafeIntegerLiteral } from './jsonNumbers.ts';

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

  it('leaves fractional and exponent numbers to schema validation', () => {
    expect(findUnsafeIntegerLiteral('[1.5, 12345678901234567890.5, 1e400]')).toBeNull();
  });
});
