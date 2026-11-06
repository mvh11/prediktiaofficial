const kickoffFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZoneName: 'short',
});

/** Formats an ISO timestamp (with offset) in the viewer's locale and time zone. */
export function formatKickoff(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : kickoffFormatter.format(date);
}

const DAY_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

/** True for a real calendar day in `YYYY-MM-DD` form. */
export function isIsoDay(value: string): boolean {
  if (!DAY_PATTERN.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** Parses a strictly positive integer from a URL/form string. */
export function parsePositiveInt(value: string | null | undefined): number | undefined {
  if (value === null || value === undefined || !/^\d{1,9}$/.test(value)) return undefined;
  const parsed = Number(value);
  return parsed > 0 ? parsed : undefined;
}
