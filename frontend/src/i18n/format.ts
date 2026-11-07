/**
 * Locale- and time-zone-aware formatting. The backend stores `kickoff_at` as
 * an absolute instant with an explicit UTC offset; it does not expose the
 * venue's time zone. The interface therefore shows every instant in one
 * declared display zone (continental Chile) and, where precision matters, also
 * in UTC — never an unlabelled local time.
 */
export interface Formatters {
  /** Compact date and time in the display zone, e.g. "sáb, 5 ene 2030, 12:00". */
  kickoff(iso: string): string;
  /** Long day in the display zone, e.g. "sábado, 5 de enero de 2030". */
  day(iso: string): string;
  /** Calendar day in the display zone as `YYYY-MM-DD` (grouping key). */
  dayKey(iso: string): string;
  /** Hour and minute in the display zone, 24-hour clock. */
  time(iso: string): string;
  /** Offset of the display zone at that instant, e.g. "UTC−3". */
  offset(iso: string): string;
  /** The same instant in UTC, e.g. "2030-01-05 15:00 UTC". */
  utc(iso: string): string;
  /** A `YYYY-MM-DD` day as a readable date without changing its calendar day. */
  isoDay(day: string): string;
  /** Integer with locale grouping. */
  integer(value: number | bigint): string;
}

function toDate(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function createFormatters(locale: string, timeZone: string): Formatters {
  const kickoff = new Intl.DateTimeFormat(locale, {
    timeZone,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  });
  const day = new Intl.DateTimeFormat(locale, { timeZone, weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' });
  const time = new Intl.DateTimeFormat(locale, { timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  const offset = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'shortOffset' });
  const isoDay = new Intl.DateTimeFormat(locale, { timeZone: 'UTC', day: 'numeric', month: 'short', year: 'numeric' });
  const integer = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 });

  const withDate = (iso: string, format: (date: Date) => string) => {
    const date = toDate(iso);
    return date === null ? iso : format(date);
  };

  return {
    kickoff: (iso) => withDate(iso, (date) => kickoff.format(date)),
    day: (iso) => withDate(iso, (date) => day.format(date)),
    dayKey: (iso) => withDate(iso, (date) => dayKey.format(date)),
    time: (iso) => withDate(iso, (date) => time.format(date)),
    offset: (iso) =>
      withDate(iso, (date) => {
        const name = offset.formatToParts(date).find((part) => part.type === 'timeZoneName')?.value ?? '';
        // "GMT-3" -> "UTC−3" (typographic minus); "GMT" alone means UTC+0.
        const match = /^GMT([+-]\d{1,2}(?::\d{2})?)?$/.exec(name);
        if (match === null) return name;
        return match[1] === undefined ? 'UTC' : `UTC${match[1].replace('-', '−')}`;
      }),
    utc: (iso) => withDate(iso, (date) => `${date.toISOString().slice(0, 16).replace('T', ' ')} UTC`),
    isoDay: (value) => withDate(`${value}T12:00:00Z`, (date) => isoDay.format(date)),
    integer: (value) => integer.format(value),
  };
}

/** Current calendar day in UTC as `YYYY-MM-DD` (the backend filters by UTC day). */
export function utcDay(date: Date = new Date(), offsetDays = 0): string {
  const shifted = new Date(date.getTime() + offsetDays * 86_400_000);
  return shifted.toISOString().slice(0, 10);
}
