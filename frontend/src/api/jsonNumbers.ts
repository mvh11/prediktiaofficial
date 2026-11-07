/**
 * `JSON.parse` silently rounds integers beyond Number.MAX_SAFE_INTEGER
 * (9007199254740993 becomes 9007199254740992). Identifiers must never be
 * rounded, so the raw response text is scanned BEFORE parsing and the first
 * integer literal that cannot be represented exactly is returned.
 *
 * The scanner understands JSON strings (including escapes), so digits inside
 * string values are ignored. Non-integer numbers (with a fraction or exponent)
 * are not identifiers in the verified contract and are left to schema checks.
 */
const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

function isNumberChar(code: number): boolean {
  // 0-9 . e E + -
  return (code >= 48 && code <= 57) || code === 46 || code === 101 || code === 69 || code === 43 || code === 45;
}

export function findUnsafeIntegerLiteral(text: string): string | null {
  let inString = false;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (inString) {
      if (code === 92 /* \ */) index += 1;
      else if (code === 34 /* " */) inString = false;
      continue;
    }
    if (code === 34) {
      inString = true;
      continue;
    }
    if (code === 45 || (code >= 48 && code <= 57)) {
      let end = index + 1;
      while (end < text.length && isNumberChar(text.charCodeAt(end))) end += 1;
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
