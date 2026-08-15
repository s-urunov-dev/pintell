import { useEffect, useState } from 'react';

import type { ResolvedDeadline } from '../api/types';
import { useI18n } from '../i18n';

/**
 * The submission deadline, ticking.
 *
 * This is the single most consequential number on the page: acting on a wrong
 * one costs someone a bid. So the component only ever counts down to an
 * instant the backend could pin to a real time zone. When it could not
 * (`deadline` is null), the caller falls back to whole days — see
 * `relativeDeadline` — rather than implying a precision the data lacks.
 *
 * Ticks once a second under a day, once a minute above it: a 40-day countdown
 * re-rendering every second would burn battery to animate a digit nobody is
 * watching.
 */

interface Parts {
  days: number;
  hours: number;
  minutes: number;
  seconds: number;
  totalMs: number;
}

function partsUntil(target: number): Parts {
  const totalMs = Math.max(0, target - Date.now());
  const totalSeconds = Math.floor(totalMs / 1000);
  return {
    days: Math.floor(totalSeconds / 86400),
    hours: Math.floor((totalSeconds % 86400) / 3600),
    minutes: Math.floor((totalSeconds % 3600) / 60),
    seconds: totalSeconds % 60,
    totalMs,
  };
}

const pad = (value: number) => String(value).padStart(2, '0');

/**
 * "Asia/Tashkent" → "Tashkent".
 *
 * The IANA name is what the backend can justify and what the API carries, but
 * the continent prefix and the underscores are machine punctuation. The city
 * is the part a reader recognises, and it is the part that disambiguates —
 * nobody needed to be told Tashkent is in Asia.
 */
function zoneLabel(zone: string): string {
  const city = zone.split('/').pop() ?? zone;
  return city.replace(/_/g, ' ');
}

export default function DeadlineCountdown({
  deadline,
  isOpen,
}: {
  deadline: ResolvedDeadline;
  isOpen: boolean | null;
}) {
  const { t, formatMoment, viewerTimeZone } = useI18n();
  const target = new Date(deadline.at).getTime();
  const [parts, setParts] = useState<Parts>(() => partsUntil(target));

  useEffect(() => {
    setParts(partsUntil(target));
    // Under a day every second matters; above it, a minute is plenty.
    const period = partsUntil(target).totalMs < 86_400_000 ? 1000 : 60_000;
    const timer = setInterval(() => setParts(partsUntil(target)), period);
    return () => clearInterval(timer);
  }, [target]);

  const closed = parts.totalMs === 0 || isOpen === false;
  // Under 24h is the band where a few hours of error would actually mislead,
  // so it is both the urgent styling and where the zone caveat is shown.
  const urgent = !closed && parts.totalMs < 86_400_000;
  const tone = closed ? 'closed' : urgent ? 'urgent' : 'open';

  return (
    <div className={`countdown countdown-${tone}`} role="timer">
      <span className="countdown-label">{t('countdown.label')}</span>

      {closed ? (
        <strong className="countdown-value countdown-closed">
          {t('countdown.closed')}
        </strong>
      ) : (
        <strong className="countdown-value">
          {parts.days > 0 && (
            <span className="countdown-unit">
              <em>{parts.days}</em>
              <small>{t('countdown.days')}</small>
            </span>
          )}
          <span className="countdown-unit">
            <em>{pad(parts.hours)}</em>
            <small>{t('countdown.hours')}</small>
          </span>
          <span className="countdown-unit">
            <em>{pad(parts.minutes)}</em>
            <small>{t('countdown.minutes')}</small>
          </span>
          {/* Seconds only below a day — above that they are visual noise. */}
          {parts.days === 0 && (
            <span className="countdown-unit">
              <em>{pad(parts.seconds)}</em>
              <small>{t('countdown.seconds')}</small>
            </span>
          )}
        </strong>
      )}

      {/* One time, and it is the reader's own.

          There used to be a second line under this one printing the borrower's
          wall clock whenever the two zones differed. The argument for it was
          that a vendor phoning the borrower will hear that number — true, and
          not worth what it cost: two times side by side is a question, and the
          reader has to answer it correctly before they can act. They are the
          same instant, and the instant is what a calendar takes. So the page
          converts, once, and says which zone it converted to. */}
      <span className="countdown-target">
        {formatMoment(deadline.at)}
        <small className="countdown-zone">{zoneLabel(viewerTimeZone)}</small>
      </span>

      {/* Shown only when it could change what someone does: the zone is a
          guess AND the deadline is close enough for hours to matter. */}
      {deadline.approximate && !closed && urgent && (
        <span className="countdown-caveat">
          {t('countdown.approximate', { zone: zoneLabel(deadline.timezone) })}
        </span>
      )}
    </div>
  );
}
