/**
 * Calendar arithmetic for the review series.
 *
 * Snapshot dates are plain `YYYY-MM-DD` strings stamped in Moscow time, so
 * every step here works in UTC — a local-time cursor would skip or repeat a
 * day whenever the browser sits in a different zone.
 */

export function previousDay(date: string): string {
  return shiftDay(date, -1);
}

export function shiftDay(date: string, days: number): string {
  const cursor = new Date(`${date}T00:00:00Z`);
  cursor.setUTCDate(cursor.getUTCDate() + days);
  return cursor.toISOString().slice(0, 10);
}

/** Every date from `from` to `to`, inclusive. */
export function daysBetween(from: string, to: string): string[] {
  const dates: string[] = [];
  const cursor = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  while (cursor <= end) {
    dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return dates;
}

/** The last `count` days, ending on `end`. */
export function daysEndingAt(end: string, count: number): string[] {
  return daysBetween(shiftDay(end, -(count - 1)), end);
}

/** Today's date in Moscow, which is the day the snapshots are stamped with. */
export function moscowToday(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "Europe/Moscow",
  }).formatToParts(now);
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}
