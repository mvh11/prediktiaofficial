/**
 * Numeric identifiers.
 *
 * Verified contract: competition, team, fixture and season ids are PostgreSQL
 * `integer` columns (backend/app/models/*.py), i.e. 1..2,147,483,647, sent as
 * JSON numbers. In the browser they are kept as their canonical decimal string
 * in URLs and only converted to `number` once proven to be in range, so a value
 * is never silently rounded or truncated. Anything else is rejected and the
 * user is told why; no request is made for it.
 */

/** Largest value of the backend's PostgreSQL `integer` primary keys. */
export const MAX_DB_INT = 2_147_483_647;

const CANONICAL_POSITIVE = /^[1-9]\d*$/;

export type ParsedId =
  /** In range: safe to send to the backend. `canonical` is the exact decimal string. */
  | { readonly kind: 'valid'; readonly value: number; readonly canonical: string }
  /** A canonical positive integer, but beyond the backend range (possibly beyond JS precision). */
  | { readonly kind: 'unsupported'; readonly raw: string }
  /** Not a canonical positive integer (letters, sign, decimals, leading zeros, empty). */
  | { readonly kind: 'invalid'; readonly raw: string };

export function parseId(raw: string | null | undefined): ParsedId {
  const text = raw ?? '';
  if (!CANONICAL_POSITIVE.test(text)) return { kind: 'invalid', raw: text };
  // Compare as text first so huge values never pass through a lossy Number conversion.
  const maxText = String(MAX_DB_INT);
  if (text.length > maxText.length || (text.length === maxText.length && text > maxText)) {
    return { kind: 'unsupported', raw: text };
  }
  return { kind: 'valid', value: Number(text), canonical: text };
}

/** Convenience for optional filters: the in-range value, or undefined. */
export function parsePositiveInt(raw: string | null | undefined): number | undefined {
  const parsed = parseId(raw);
  return parsed.kind === 'valid' ? parsed.value : undefined;
}
