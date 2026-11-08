/**
 * Raw JSON for precision tests. JSON.stringify can only emit canonical numbers, but the tokens under test
 * (`2029.9999999999999999`, `6.7e1`, `-0`…) must reach the HTTP client exactly as a server could send them.
 */
export const RAW_TOKEN = 987_654_321;

/** JSON text of `value` with its single RAW_TOKEN number replaced by `token`, verbatim. */
export function withRawToken(value: unknown, token: string): string {
  const text = JSON.stringify(value);
  const parts = text.split(String(RAW_TOKEN));
  if (parts.length !== 2) throw new Error(`RAW_TOKEN must occur exactly once, found ${String(parts.length - 1)}`);
  return parts.join(token);
}
