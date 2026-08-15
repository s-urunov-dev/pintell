import { Link, useNavigate, useParams } from 'react-router-dom';

import { fetchTeamLead } from '../api/client';
import type { TeamLeadDetail } from '../api/types';
import { DetailSkeleton, ErrorState } from '../components/StateViews';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n, type MessageKey } from '../i18n';
import { categoryLabel } from '../lib/categories';

const LINK_LABEL: Record<string, MessageKey> = {
  worldbank: 'contacts.profileLink',
  publication: 'contacts.publicationLink',
  profile: 'contacts.profileLink',
};

/**
 * One World Bank team lead.
 *
 * Two halves, and the second is the one worth the page. The top is what a
 * search found published about them — title, unit, duty station, a work
 * address, professional links — and it is often thin, because the Bank
 * publishes very little about individual staff. The bottom needs no search at
 * all: the projects they lead in this database and every tender those projects
 * issued, which is exact rather than inferred and answers what a bidder
 * actually wants to know.
 *
 * What is not here is deliberate. No personal social accounts, messaging
 * handles or photographs: these are named private individuals rather than
 * public figures, and gathering their personal presence onto one page would be
 * a dossier no matter how each fragment was obtained.
 */
export default function TeamLeadDetailPage() {
  const { leadId = '' } = useParams();
  const navigate = useNavigate();
  const { t, tv, formatDate, formatDateTime } = useI18n();

  const { data, loading, error, reload } = useAsyncData<TeamLeadDetail>(
    (signal) => fetchTeamLead(leadId, signal),
    [leadId],
  );

  if (loading) return <DetailSkeleton />;

  if (error != null) {
    return (
      <>
        <BackLink onClick={() => navigate(-1)} />
        <ErrorState error={error} onRetry={reload} />
      </>
    );
  }

  if (!data) return null;

  const subtitle = [data.title, data.unit].filter(Boolean).join(' · ');
  const neverLookedUp = data.checked_at === null;

  return (
    <article className="detail">
      <BackLink onClick={() => navigate(-1)} />

      <div className="detail-grid">
        <div className="detail-main">
          <header className="detail-head">
            <div className="detail-tags">
              <span className="tag">{data.organization}</span>
              {data.country_office && <span className="tag tag-quiet">{data.country_office}</span>}
            </div>

            <div className="lead-identity">
              {/* Referenced from the Bank's CDN, never re-hosted: the portrait
                  stays theirs to change or withdraw. */}
              {data.photo_url && (
                <img
                  className="lead-photo"
                  src={data.photo_url}
                  alt=""
                  loading="lazy"
                  referrerPolicy="no-referrer"
                />
              )}
              <div>
                <h1>{data.name}</h1>
                {subtitle && <p className="lead">{subtitle}</p>}
              </div>
            </div>

            {/* The Bank's own words outrank the model's one-liner. */}
            {data.bio ? (
              <p className="muted lead-note">{data.bio}</p>
            ) : (
              data.summary && <p className="muted lead-note">{data.summary}</p>
            )}

            <dl className="fact-strip">
              <Fact label={t('lead.projects')} value={String(data.stats.projects)} />
              <Fact label={t('lead.notices')} value={String(data.stats.notices)} />
              <Fact
                label={t('lead.openNotices')}
                value={String(data.stats.open_notices)}
                strong={data.stats.open_notices > 0}
              />
            </dl>
          </header>

          {/* The half that needs no search: what this database already knows. */}
          <section className="card">
            <h2 className="section-title">{t('lead.projectsTitle')}</h2>
            {data.projects.length === 0 ? (
              <p className="muted small">{t('lead.noProjects')}</p>
            ) : (
              <ul className="lead-projects">
                {data.projects.map((project) => (
                  <li key={project.project_id}>
                    <div>
                      <strong>{project.name || project.project_id}</strong>
                      <p className="muted small">
                        {[
                          project.project_id,
                          tv('country', project.country),
                          project.implementing_agency,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </p>
                    </div>
                    {project.total_amount_display && (
                      <span className="lead-amount">USD {project.total_amount_display}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card">
            <h2 className="section-title">{t('lead.noticesTitle')}</h2>
            {data.notices.length === 0 ? (
              <p className="muted small">{t('lead.noNotices')}</p>
            ) : (
              <ul className="lead-notices">
                {data.notices.map((notice) => (
                  <li key={notice.id}>
                    <Link to={`/tenders/${notice.id}`}>
                      <span className="lead-notice-title">
                        {notice.title || notice.id}
                      </span>
                    </Link>
                    <p className="muted small">
                      {[
                        tv('noticeType', notice.notice_type),
                        tv('country', notice.country),
                        notice.category && notice.category !== 'unknown'
                          ? categoryLabel(notice.category, t)
                          : '',
                        notice.deadline_date ? formatDate(notice.deadline_date) : '',
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                      {notice.is_open && (
                        <span className="lead-open">{t('lead.open')}</span>
                      )}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <aside className="detail-side">
          <div className="side-sticky">
            <section className="side-block">
              <h2 className="side-title">{t('lead.published')}</h2>

              {neverLookedUp ? (
                /* Distinguishes "nothing was found" from "nobody has looked",
                   which are very different states to a reader. */
                <p className="muted small">{t('lead.notCheckedYet')}</p>
              ) : (
                <>
                  <dl className="kv-list kv-tight">
                    <Fact label={t('lead.title')} value={data.title} />
                    <Fact label={t('lead.unit')} value={data.unit} />
                    <Fact label={t('lead.office')} value={data.country_office} />
                  </dl>

                  {data.work_email ? (
                    <p className="contact-line">
                      <a href={`mailto:${data.work_email}`}>{data.work_email}</a>
                      <span
                        className={`contact-flag ${
                          data.email_confirmed ? 'is-confirmed' : 'is-derived'
                        }`}
                      >
                        {t(
                          data.email_confirmed
                            ? 'contacts.emailConfirmed'
                            : 'contacts.emailUnconfirmed',
                        )}
                      </span>
                    </p>
                  ) : (
                    <p className="muted small">{t('contacts.noEmail')}</p>
                  )}

                  {data.bank_page_url && (
                    <p className="contact-line">
                      <a
                        href={data.bank_page_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        {t('lead.bankPage')}
                      </a>
                    </p>
                  )}

                  {data.links.length > 0 && (
                    <p className="contact-line contact-links">
                      {data.links.map((link) => (
                        <a
                          key={link.url}
                          href={link.url}
                          target="_blank"
                          rel="noopener noreferrer nofollow"
                        >
                          {t(LINK_LABEL[link.kind] ?? 'contacts.otherLink')}
                        </a>
                      ))}
                    </p>
                  )}

                  <p className="muted small lead-checked">
                    {t('lead.checked', { when: formatDateTime(data.checked_at) })}
                  </p>
                </>
              )}
            </section>

            {/* Says plainly what this page will never show, so its absence
                reads as a decision rather than a gap to be filled later. */}
            <section className="side-block">
              <h2 className="side-title">{t('lead.scopeTitle')}</h2>
              <p className="muted small">{t('lead.scopeNote')}</p>
            </section>
          </div>
        </aside>
      </div>
    </article>
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
  return (
    <button type="button" className="back-link" onClick={onClick}>
      <span aria-hidden="true">←</span> {t('detail.back')}
    </button>
  );
}
