/**
 * Guards for numbers that `JSON.parse` cannot hand over faithfully. Both work on
 * the RAW response text, because once parsed the original token is gone:
 *
 * - `JSON.parse` rounds integers beyond Number.MAX_SAFE_INTEGER
 *   (9007199254740993 becomes 9007199254740992);
 * - it also rounds near-integers and accepts exponents, so an identifier token
 *   such as `12.9999999999999999`, `1.0000000000000001` or `1.3e1` arrives as a
 *   perfectly "safe" integer (13, 1, 13). No check after parsing can tell.
 *
 * Both scanners understand JSON strings (including escapes), so text inside
 * string values is never mistaken for a number.
 */
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

function isNumberChar(code: number): boolean {
  // 0-9 . e E + -
  return (code >= 48 && code <= 57) || code === 46 || code === 101 || code === 69 || code === 43 || code === 45;
}

/** Index just after the closing quote of the JSON string that starts at `start`. */
function stringEnd(text: string, start: number): number {
  for (let index = start + 1; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code === 92 /* \ */) index += 1;
    else if (code === 34 /* " */) return index + 1;
  }
  return text.length;
}

function numberEnd(text: string, start: number): number {
  let end = start + 1;
  while (end < text.length && isNumberChar(text.charCodeAt(end))) end += 1;
  return end;
}

/** First integer literal anywhere in the text that cannot be represented exactly as a JS number, else null. */
export function findUnsafeIntegerLiteral(text: string): string | null {
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code === 34) {
      index = stringEnd(text, index) - 1;
      continue;
    }
    if (code === 45 || (code >= 48 && code <= 57)) {
      const end = numberEnd(text, index);
      const token = text.slice(index, end);
      if (/^-?\d+$/.test(token)) {
        const digits = token.startsWith('-') ? token.slice(1) : token;
        if (digits.length >= 16 && BigInt(digits) > MAX_SAFE) return token;
      }
      index = end - 1;
    }
  }
  return null;
}

/**
 * Identifier fields of the verified contract and the exact token each must be:
 * a canonical decimal integer (no sign unless allowed, no leading zeros, no
 * fraction, no exponent) within the PostgreSQL `integer` range of its column
 * (backend/app/models/*.py). Keys are matched at any depth, so nested team ids
 * (`home_team.id`) are covered too.
 */
const INT32_MIN = -2_147_483_648n;
const INT32_MAX = 2_147_483_647n;
const POSITIVE = /^[1-9]\d*$/;
const SIGNED = /^-?(0|[1-9]\d*)$/;

export const IDENTIFIER_RULES: Readonly<Record<string, { readonly pattern: RegExp; readonly min: bigint; readonly max: bigint }>> = {
  /** Primary keys (serial `integer`): fixtures, competitions, teams. */
  id: { pattern: POSITIVE, min: 1n, max: INT32_MAX },
  competition_id: { pattern: POSITIVE, min: 1n, max: INT32_MAX },
  /** Provider ids: `Integer` columns without a sign constraint. */
  external_id: { pattern: SIGNED, min: INT32_MIN, max: INT32_MAX },
};

export function isValidIdentifierToken(key: string, token: string): boolean {
  const rule = IDENTIFIER_RULES[key];
  if (rule === undefined) return true;
  if (!rule.pattern.test(token) || token === '-0') return false;
  const value = BigInt(token);
  return value >= rule.min && value <= rule.max;
}

export interface IdentifierIssue {
  /** The offending token exactly as received. */
  readonly literal: string;
  /** The identifier key it was the value of, e.g. "id" or "competition_id". */
  readonly key: string;
  /** 1-based record position when the response is a top-level array. */
  readonly row?: number;
}

interface Frame {
  readonly kind: 'object' | 'array';
  key: string | null;
  expectKey: boolean;
  index: number;
}

/**
 * First identifier whose RAW token is not acceptable (see IDENTIFIER_RULES),
 * else null. Requires syntactically valid JSON (call it after JSON.parse
 * succeeded). Keys are decoded with JSON.parse, so escaped spellings such as
 * "id" are recognised; duplicate keys are all checked.
 */
export function findIdentifierIssue(text: string): IdentifierIssue | null {
  const stack: Frame[] = [];
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    const top = stack.at(-1);
    if (code === 34 /* " */) {
      const end = stringEnd(text, index);
      if (top?.kind === 'object' && top.expectKey) {
        top.key = JSON.parse(text.slice(index, end)) as string;
        top.expectKey = false;
      }
      index = end - 1;
    } else if (code === 123 /* { */) {
      stack.push({ kind: 'object', key: null, expectKey: true, index: 0 });
    } else if (code === 91 /* [ */) {
      stack.push({ kind: 'array', key: null, expectKey: false, index: 0 });
    } else if (code === 125 /* } */ || code === 93 /* ] */) {
      stack.pop();
    } else if (code === 44 /* , */) {
      if (top?.kind === 'object') top.expectKey = true;
      else if (top) top.index += 1;
    } else if (code === 45 || (code >= 48 && code <= 57)) {
      const end = numberEnd(text, index);
      const token = text.slice(index, end);
      if (top?.kind === 'object' && top.key !== null && !isValidIdentifierToken(top.key, token)) {
        const root = stack[0];
        const row = root?.kind === 'array' && stack.length >= 2 ? root.index + 1 : undefined;
        return { literal: token, key: top.key, ...(row !== undefined ? { row } : {}) };
      }
      index = end - 1;
    }
  }
  return null;
}
