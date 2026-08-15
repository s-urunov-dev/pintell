import type { MessageParams, TKey } from '../i18n';

type Translate = (key: TKey, params?: MessageParams) => string;

/**
 * Date, number and currency formatting now live on the i18n context
 * (`useI18n()`), because a formatter has to be rebuilt when the locale
 * changes. What remains here is locale-independent text handling, plus the
 * deadline phrasing — which takes `t` so the caller supplies the language.
 */

/** "3 days left" / "Closes today" / "Closed 2 days ago" — deliberately coarse. */
export function relativeDeadline(days: number | null | undefined, t: Translate): string {
  if (days === null || days === undefined) return t('deadline.none');
  if (days > 0) return t('deadline.left', { count: days });
  if (days === 0) return t('deadline.today');
  if (days === -1) return t('deadline.yesterday');
  return t('deadline.ago', { count: Math.abs(days) });
}

/**
 * How long is left, in the unit that is actually informative at that distance.
 *
 * Two bands, and the boundary between them is where a day stops being a useful
 * unit. Under twenty-four hours the answer is hours: a tender closing at 01:00
 * tomorrow is one calendar day away and four hours away, and "1 day left" is
 * the reading that costs someone the bid. Above it, whole days counted on the
 * reader's own calendar — see `calendarDaysUntil` for why the API's figure
 * cannot answer that.
 *
 * Both bands are derived from the same instant the countdown on the detail page
 * runs against, so the list and the tender can never tell different stories.
 * With no instant to work from the coarse server figure is all there is, and
 * the phrasing falls back to whole days — which is honest, because a notice
 * whose zone the backend would not guess has no hour anyone can stand behind.
 */
export function deadlineLabel(
  at: string | null | undefined,
  serverDays: number | null | undefined,
  timeZone: string,
  t: Translate,
  now: Date = new Date(),
): string {
  const days = calendarDaysUntil(at, timeZone, now);
  if (days === null || !at) return relativeDeadline(serverDays, t);

  const hours = (new Date(at).getTime() - now.getTime()) / 3_600_000;
  if (hours <= 0) return relativeDeadline(days, t);
  if (hours < 24) {
    // Rounded up: with fifty minutes left, "1 hour" overstates what is left by
    // less than "0 hours" understates it, and the countdown beside it is exact.
    const remaining = Math.ceil(hours);
    return remaining <= 1 ? t('deadline.lastHour') : t('deadline.hours', { count: remaining });
  }
  return relativeDeadline(days, t);
}

/**
 * Whole calendar days from today to a deadline, counted in `timeZone`.
 *
 * This exists because the number the API sends cannot be right. The backend
 * computes `days_until_deadline` as `(deadline_at - now).days` — a *duration*
 * truncated to whole days, on a server that runs in UTC — and the card reads 0
 * as "closes today". Zero there means "less than twenty-four hours from now",
 * which is not the same statement: a tender closing tomorrow at 09:00 is
 * labelled "closes today" from 09:00 this morning onward, so the card says
 * today while the detail page counts down to a date that is not today. It also
 * cannot account for where the reader is, because it was computed before anyone
 * asked.
 *
 * So the count is done here, from the instant the backend *can* justify
 * (`deadline.at`, pinned to a real zone by `apps.tenders.deadlines`), against
 * the reader's own calendar. Today is today, tomorrow is one day, and both mean
 * the same thing to the countdown beside them. Note what stays on the server:
 * the instant. Nothing here re-derives when a tender closes — it only asks
 * which of the reader's days that instant falls on.
 *
 * `null` when there is no instant, which is the backend refusing to place a
 * country on the map; the caller falls back to the coarse server figure.
 */
export function calendarDaysUntil(
  at: string | null | undefined,
  timeZone: string,
  now: Date = new Date(),
): number | null {
  if (!at) return null;
  const target = new Date(at);
  if (Number.isNaN(target.getTime())) return null;

  const start = startOfDay(now, timeZone);
  const end = startOfDay(target, timeZone);
  if (start === null || end === null) return null;
  // Both are midnight UTC of a local calendar date, so the difference is a
  // whole number of days and no daylight-saving change can make it a fraction.
  return Math.round((end - start) / 86_400_000);
}

/**
 * Midnight UTC of the calendar date `date` falls on in `timeZone`.
 *
 * Built from `formatToParts` rather than from an offset calculation because
 * `Intl` is the only thing in the browser that knows a zone's history, and the
 * deadlines this serves are in zones that have changed their offset.
 */
function startOfDay(date: Date, timeZone: string): number | null {
  try {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    }).formatToParts(date);
    const get = (type: Intl.DateTimeFormatPartTypes) =>
      Number(parts.find((part) => part.type === type)?.value);
    return Date.UTC(get('year'), get('month') - 1, get('day'));
  } catch {
    // An unknown zone name. Refusing is right: the caller then shows the
    // server's coarse figure rather than a count against the wrong calendar.
    return null;
  }
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}

export function titleOf(notice: { bid_description: string; project_name: string; id: string }): string {
  return notice.bid_description?.trim() || notice.project_name?.trim() || notice.id;
}
