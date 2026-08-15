import { Link } from 'react-router-dom';

import type { TenderListItem } from '../api/types';
import { useI18n } from '../i18n';
import { categoryShort, subcategoryLabel, subcategoryShort } from '../lib/categories';
import { calendarDaysUntil, deadlineLabel, titleOf, truncate } from '../lib/format';

function deadlineTone(notice: TenderListItem, days: number | null): string {
  if (notice.is_open === null) return 'neutral';
  if (!notice.is_open) return 'closed';
  return (days ?? 0) <= 7 ? 'urgent' : 'open';
}

export default function TenderCard({ notice }: { notice: TenderListItem }) {
  const { t, tv, formatDate, formatDay, viewerTimeZone } = useI18n();
  // The reader's calendar, not the server's duration — see `calendarDaysUntil`
  // for why the API's own figure cannot answer this. Its own figure stays as
  // the fallback for the notices whose country the backend will not place on
  // the map, where whole days are all anyone can honestly claim.
  const days =
    calendarDaysUntil(notice.deadline?.at, viewerTimeZone) ??
    notice.days_until_deadline ??
    null;
  const tone = deadlineTone(notice, days);
  const subLabel = subcategoryShort(notice.subcategory, t);

  return (
    <article className="card tender-card">
      <div className="card-top">
        <span className="tag">
          {tv('noticeType', notice.notice_type) || t('card.noticeFallback')}
        </span>
        {notice.category && notice.category !== 'unknown' && (
          <span className={`tag tag-category cat-${notice.category}`}>
            {categoryShort(notice.category, t)}
            {/* The sub-direction rides inside the category chip rather than
                taking a third one: it is a refinement of that tag, and a
                separate chip would push the deadline off the row. */}
            {subLabel && (
              // The chip truncates on a narrow card, so the full name has to
              // stay reachable somewhere.
              <span className="tag-sub" title={subcategoryLabel(notice.subcategory, t)}>
                {subLabel}
              </span>
            )}
          </span>
        )}
        <span className={`status-dot status-${tone}`} aria-hidden="true" />
        <span className={`status-text status-${tone}`}>
          {deadlineLabel(notice.deadline?.at, notice.days_until_deadline, viewerTimeZone, t)}
        </span>
      </div>

      <h2 className="card-title">
        <Link to={`/tenders/${encodeURIComponent(notice.id)}`}>
          {truncate(titleOf(notice), 140)}
        </Link>
      </h2>

      {notice.project_name && (
        <p className="card-subtitle">{truncate(notice.project_name, 110)}</p>
      )}

      <dl className="card-meta">
        <div>
          <dt>{t('card.country')}</dt>
          <dd>{tv('country', notice.country) || '—'}</dd>
        </div>
        <div>
          <dt>{t('card.method')}</dt>
          <dd title={tv('procurementMethod', notice.procurement_method_name)}>
            {notice.procurement_method_code || '—'}
          </dd>
        </div>
        <div>
          <dt>{t('card.published')}</dt>
          <dd>{formatDate(notice.notice_date)}</dd>
        </div>
        <div>
          <dt>{t('card.deadline')}</dt>
          {/* The reader's own date, derived from the same instant the label
              above counts against, so the two can never disagree.
              `deadline_date` is the fallback and is read in UTC — it is a bare
              calendar date with no clock, and shifting it into a zone would
              move a day that was never a moment. */}
          <dd>
            {notice.deadline
              ? formatDay(notice.deadline.at)
              : formatDate(notice.deadline_date)}
          </dd>
        </div>
      </dl>

      <div className="card-footer">
        <span className="ref">{notice.bid_reference_no || notice.id}</span>
        <Link className="link-arrow" to={`/tenders/${encodeURIComponent(notice.id)}`}>
          {t('card.viewDetails')} <span aria-hidden="true">→</span>
        </Link>
      </div>
    </article>
  );
}
