import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { fetchNoticeDocuments, fetchTender } from '../api/client';
import type {
  ContractAward,
  PendingProjectDocuments,
  ProjectDocuments,
  TenderDetail,
} from '../api/types';
import ContactsPanel from '../components/ContactsPanel';
import DeadlineCountdown from '../components/DeadlineCountdown';
import NoticeExpertsPanel from '../components/NoticeExpertsPanel';
import ProjectDocumentsPanel from '../components/ProjectDocumentsPanel';
import SafeHtml from '../components/SafeHtml';
import SimilarWinnersPanel from '../components/SimilarWinnersPanel';
import TorPanel from '../components/TorPanel';
import { DetailSkeleton, ErrorState } from '../components/StateViews';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';
import {
  categoryLabel,
  categoryOrigin,
  subcategoryLabel,
  subcategoryShort,
} from '../lib/categories';
import { relativeDeadline, titleOf } from '../lib/format';

/** Above this, the notice body is collapsed so it cannot bury the panel. */
const COLLAPSE_BODY_OVER_CHARS = 2200;

export default function TenderDetailPage() {
  const { noticeId = '' } = useParams();
  const navigate = useNavigate();
  const { t, tv, formatDate, formatDateTime, formatNumber, formatPercent } = useI18n();
  const [bodyExpanded, setBodyExpanded] = useState(false);

  const { data, loading, error, reload } = useAsyncData<TenderDetail>(
    (signal) => fetchTender(noticeId, signal),
    [noticeId],
  );

  // The project's document set is a second request: it is larger, slower, and
  // useful even while the notice itself is still rendering.
  const documents = useAsyncData<ProjectDocuments | PendingProjectDocuments>(
    (signal) => fetchNoticeDocuments(noticeId, signal),
    [noticeId],
  );

  if (loading) return <DetailSkeleton />;

  if (error != null) {
    return (
      <>
        <BackLink />
        <ErrorState error={error} onRetry={reload} />
      </>
    );
  }

  if (!data) return null;

  const tone = data.is_open === null ? 'neutral' : data.is_open ? 'open' : 'closed';
  const body = data.notice_text_sanitized ?? '';
  const longBody = body.length > COLLAPSE_BODY_OVER_CHARS;

  return (
    <article className="detail">
      <BackLink onClick={() => navigate(-1)} />

      <div className="detail-grid">
        {/* ---- main column ------------------------------------------------ */}
        <div className="detail-main">
          <header className="detail-head">
            <div className="detail-tags">
              <span className="tag">
                {tv('noticeType', data.notice_type) || t('card.noticeFallback')}
              </span>
              {data.category && data.category !== 'unknown' && (
                <span
                  className={`tag tag-category cat-${data.category}`}
                  title={`${categoryOrigin(data.category_source, t)}${
                    data.category_rationale ? ` — ${data.category_rationale}` : ''
                  }`}
                >
                  {categoryLabel(data.category, t)}
                </span>
              )}
              {/* Spelt out in full here — there is room, and the confidence
                  belongs in the tooltip next to it. */}
              {subcategoryShort(data.subcategory, t) && (
                <span
                  className="tag tag-subcategory"
                  title={
                    data.subcategory_confidence !== null
                      ? t('detail.subConfidence', {
                          percent: formatPercent(data.subcategory_confidence),
                        })
                      : undefined
                  }
                >
                  {subcategoryLabel(data.subcategory, t)}
                </span>
              )}
              {data.notice_status && (
                <span className="tag tag-quiet">{tv('status', data.notice_status)}</span>
              )}
              {/* Only when there is no exact instant to count down to. */}
              {!data.deadline && (
                <span className={`status-text status-${tone}`}>
                  {relativeDeadline(data.days_until_deadline, t)}
                </span>
              )}
            </div>

            <h1>{titleOf(data)}</h1>
            {data.project_name && <p className="lead">{data.project_name}</p>}

            {/* The facts someone scans before deciding to read anything. */}
            <dl className="fact-strip">
              <Fact label={t('detail.fact.country')} value={tv('country', data.country)} />
              <Fact
                label={t('detail.fact.method')}
                value={
                  tv('procurementMethod', data.procurement_method_name) ||
                  data.procurement_method_code
                }
              />
              <Fact label={t('detail.fact.published')} value={formatDate(data.notice_date)} />
              <Fact label={t('detail.fact.reference')} value={data.bid_reference_no} />
              {data.project?.total_amount_display && (
                <Fact
                  label={t('detail.budget')}
                  value={`USD ${data.project.total_amount_display}`}
                  strong
                />
              )}
              {data.project?.implementing_agency && (
                <Fact label={t('detail.agency')} value={data.project.implementing_agency} />
              )}
            </dl>
          </header>

          {data.award && <AwardPanel award={data.award} />}

          <section className="card">
            <h2 className="section-title">{t('detail.noticeText')}</h2>
            {/* Sanitised server-side, then re-parsed through an allow-list here:
                SafeHtml never uses dangerouslySetInnerHTML. */}
            <div className={longBody && !bodyExpanded ? 'notice-clamp' : undefined}>
              <SafeHtml className="notice-body" html={body} />
            </div>
            {longBody && (
              <button
                type="button"
                className="btn btn-ghost btn-sm notice-toggle"
                onClick={() => setBodyExpanded((open) => !open)}
                aria-expanded={bodyExpanded}
              >
                {t(bodyExpanded ? 'detail.hideFullText' : 'detail.showFullText')}
              </button>
            )}
          </section>

          <ProjectDocumentsPanel
            projectId={data.project_id}
            data={documents.data}
            loading={documents.loading}
            error={documents.error}
          />

          {/* The team the tender names, on the public page rather than only
              behind the eligibility check. Reading what a tender demands has
              always been public (D18) and this is the same kind of statement —
              quoted, from the notice's own text. Requiring an account to see
              which experts a tender needs would hide the thing most likely to
              bring a vendor back. Renders nothing when none were found. */}
          <NoticeExpertsPanel noticeId={data.id} />

          {/* Competitive context for a decision being made now, so it belongs
              on the open tender rather than only in the awards feed. Below the
              tender's own content on purpose: it is the record of other
              contracts, not a statement about this one, and renders nothing
              when the archive holds nothing close enough. */}
          <SimilarWinnersPanel noticeId={data.id} />
        </div>

        {/* ---- sticky side panel: everything acted on without scrolling --- */}
        <aside className="detail-side">
          <div className="side-sticky">
            {data.deadline ? (
              <DeadlineCountdown deadline={data.deadline} isOpen={data.is_open} />
            ) : (
              <div className={`countdown countdown-${tone}`}>
                <span className="countdown-label">{t('countdown.label')}</span>
                <strong className="countdown-value countdown-plain">
                  {relativeDeadline(data.days_until_deadline, t)}
                </strong>
                <span className="countdown-target">{formatDate(data.deadline_date)}</span>
              </div>
            )}

            {/* Directly under the deadline: knowing *what* the work is comes
                before knowing who to ask about it. */}
            <TorPanel tor={data.tor} noticeTitle={titleOf(data)} />

            {/* Three tiers, strongest source first. Replaces the single
                contact block: the notice's own fields are tier 1 of it, and
                the two weaker sources were previously either buried or
                dropped entirely. */}
            <ContactsPanel contacts={data.contacts} noticeTitle={titleOf(data)} />

            <section className="side-block">
              <dl className="kv-list kv-tight">
                <Fact label={t('detail.fact.methodCode')} value={data.procurement_method_code} />
                <Fact label={t('detail.fact.projectId')} value={data.project_id} />
                <Fact label={t('detail.fact.noticeId')} value={data.id} />
                <Fact
                  label={t('detail.fact.language')}
                  value={tv('docLanguage', data.notice_language)}
                />
                {data.project?.documents_count ? (
                  <Fact
                    label={t('project.documents')}
                    value={formatNumber(data.project.documents_count)}
                  />
                ) : null}
              </dl>
            </section>

            <div className="side-actions">
              {/* The first question a vendor has about a notice: can we even
                  bid. It sits above the upstream link because answering it
                  here is the reason to be on this page rather than on the
                  Bank's. */}
              <Link
                to={`/tenders/${encodeURIComponent(data.id)}/compliance`}
                className="btn btn-primary btn-block"
              >
                {t('detail.checkEligibility')}
              </Link>
              {/* Placeholder for the win-probability feature — the button
                  reserves its place in the layout and sets the expectation. */}
              <button type="button" className="btn btn-block btn-soon" disabled>
                {t('detail.winChance')}
                <span className="btn-soon-tag">{t('detail.winChanceSoon')}</span>
              </button>
              {/* Demoted from primary when the eligibility check was added
                  above: there is one main action on this page now, and leaving
                  two buttons equally loud would have made neither read as it. */}
              <a
                className="btn btn-ghost btn-block"
                href={data.source_url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {t('detail.openUpstream')}
              </a>
              <p className="muted small">
                {t('detail.mirrored', { when: formatDateTime(data.last_synced_at) })}
              </p>
            </div>
          </div>
        </aside>
      </div>
    </article>
  );
}

/** Who won a contract, for how much — the competitor-analysis panel. */
function AwardPanel({ award }: { award: ContractAward }) {
  const { t, tv, formatDate, formatMoney } = useI18n();

  const money = (value: string | null) => {
    if (!value) return null;
    const amount = Number(value);
    if (Number.isNaN(amount)) return value;
    return formatMoney(amount, award.currency);
  };

  return (
    <section className="card award-card">
      <h2 className="section-title">{t('award.title')}</h2>

      <div className="award-winner">
        <div>
          <span className="muted small">{t('award.awardedTo')}</span>
          <h3>{award.supplier_name || t('award.notPublished')}</h3>
          {(award.supplier_country || award.supplier_address) && (
            <p className="muted small">
              {[
                tv('country', award.supplier_country),
                award.supplier_address.replace(/\n/g, ', '),
              ]
                .filter(Boolean)
                .join(' · ')}
            </p>
          )}
        </div>
        {award.supplier_website && (
          <a
            className="btn btn-ghost btn-sm"
            href={award.supplier_website}
            target="_blank"
            rel="noopener noreferrer nofollow"
          >
            {t('award.companyWebsite')}
          </a>
        )}
      </div>

      <dl className="kv-list">
        {money(award.contract_price) && (
          <div>
            <dt>{t('award.contractPrice')}</dt>
            <dd>{money(award.contract_price)}</dd>
          </div>
        )}
        {money(award.evaluated_price) && (
          <div>
            <dt>{t('award.evaluatedPrice')}</dt>
            <dd>{money(award.evaluated_price)}</dd>
          </div>
        )}
        {money(award.bid_price_opening) && (
          <div>
            <dt>{t('award.bidPriceOpening')}</dt>
            <dd>{money(award.bid_price_opening)}</dd>
          </div>
        )}
        {award.award_date && (
          <div>
            <dt>{t('award.awardDate')}</dt>
            <dd>{formatDate(award.award_date)}</dd>
          </div>
        )}
        {award.contract_duration && (
          <div>
            <dt>{t('award.duration')}</dt>
            <dd>{award.contract_duration}</dd>
          </div>
        )}
      </dl>

      {award.evaluated_bidders.length > 0 && (
        <>
          <h3 className="section-title">{t('award.otherBidders')}</h3>
          <ul className="bidder-list">
            {award.evaluated_bidders.map((bidder, index) => (
              <li key={`${bidder.name ?? 'bidder'}-${index}`}>
                <span>{bidder.name ?? t('award.unnamedBidder')}</span>
                {bidder.country && (
                  <span className="muted small"> · {tv('country', bidder.country)}</span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      {award.supplier_website_source === 'ai_web_search' && (
        <p className="muted small award-note">{t('award.websiteNote')}</p>
      )}
    </section>
  );
}

function Fact({
  label,
  value,
  strong,
}: {
  label: string;
  value: string | null;
  strong?: boolean;
}) {
  if (!value) return null;
  return (
    <div className={strong ? 'fact-strong' : undefined}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function BackLink({ onClick }: { onClick?: () => void }) {
  const { t } = useI18n();

  if (onClick) {
    return (
      <button type="button" className="back-link" onClick={onClick}>
        <span aria-hidden="true">←</span> {t('detail.back')}
      </button>
    );
  }
  return (
    <Link to="/" className="back-link">
      <span aria-hidden="true">←</span> {t('detail.back')}
    </Link>
  );
}
