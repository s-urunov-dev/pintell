import { Link } from 'react-router-dom';

import { fetchNoticeExperts } from '../api/client';
import type { NoticeExperts } from '../api/types';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';

interface NoticeExpertsPanelProps {
  noticeId: string;
}

/**
 * The team a tender names, and who our directory holds for those roles.
 *
 * The whole component is an exercise in keeping two things visually apart that
 * a reader would otherwise merge:
 *
 * * a **position** is what the tender says. It carries the sentence that says
 *   it, and that sentence is shown, because the product's claim is that every
 *   statement about a tender can be checked against the tender.
 * * a **candidate** is somebody in our directory who works that role. Nobody
 *   read them out of anything, nobody assessed them against this tender, and
 *   the note under the shortlist says so in as many words.
 *
 * They arrive as separate fields from the API for the same reason (D20). A
 * design that rendered "recommended experts for this tender" as one list would
 * put a curated row and a quoted requirement on the same footing — which is
 * exactly the confusion the expert directory was kept out of the compliance
 * app to prevent.
 *
 * Renders nothing at all when the tender names no positions and the request
 * failed or is still running: this is a panel beside the real answer, and it
 * must never be the reason a verdict page looks broken.
 */
export default function NoticeExpertsPanel({ noticeId }: NoticeExpertsPanelProps) {
  const { t } = useI18n();
  const experts = useAsyncData<NoticeExperts>(
    (signal) => fetchNoticeExperts(noticeId, signal),
    [noticeId],
  );

  // Degrade to absence, not to an error box. The notice's requirements and its
  // verdict are the page; this is an aside, and an aside that shouts about its
  // own failure costs the reader more than it gives them.
  if (experts.loading || experts.error != null || experts.data == null) return null;

  const { positions, candidates, excluded } = experts.data;
  if (positions.length === 0 && excluded.not_found === 0) return null;

  return (
    <section className="side-block">
      <h2 className="side-title">{t('noticeExperts.title')}</h2>
      <p className="muted small">{t('noticeExperts.lead')}</p>

      {positions.length === 0 ? (
        <p className="muted">{t('noticeExperts.none')}</p>
      ) : (
        <ul className="expert-positions">
          {positions.map((position) => {
            const shortlist = position.role ? (candidates[position.role] ?? []) : [];
            return (
              <li key={position.id} className="expert-position">
                <div className="expert-position-head">
                  <strong>{position.title}</strong>
                  <span className={position.is_mandatory ? 'tag tag-strong' : 'tag'}>
                    {position.is_mandatory
                      ? t('noticeExperts.mandatory')
                      : t('noticeExperts.desirable')}
                  </span>
                  {position.count > 1 && (
                    <span className="tag">
                      {t('noticeExperts.needed', { count: position.count })}
                    </span>
                  )}
                </div>

                {/* The evidence. Shown for the same reason a requirement shows
                    its quote: without it this is just an assertion about
                    somebody else's tender. */}
                <blockquote className="evidence">{position.evidence_quote}</blockquote>

                {position.role ? (
                  <div className="expert-candidates">
                    <span className="muted small">{t('noticeExperts.candidates')}</span>
                    {shortlist.length === 0 ? (
                      <p className="muted small">{t('noticeExperts.noCandidates')}</p>
                    ) : (
                      <ul className="expert-candidate-list">
                        {shortlist.map((expert) => (
                          <li key={expert.id}>
                            {expert.linkedin_url ? (
                              <a
                                href={expert.linkedin_url}
                                target="_blank"
                                rel="noopener noreferrer"
                              >
                                {expert.full_name}
                              </a>
                            ) : (
                              expert.full_name
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                    <Link className="small" to={`/experts?role=${position.role}`}>
                      {t('noticeExperts.seeAll')}
                    </Link>
                  </div>
                ) : (
                  // A position the taxonomy cannot file. Saying so is better
                  // than showing nothing: the tender still needs this person,
                  // and the gap is ours rather than theirs.
                  <p className="muted small">{t('noticeExperts.unfiled')}</p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {positions.length > 0 && (
        <p className="muted small">{t('noticeExperts.candidatesNote')}</p>
      )}

      {/* Counted, never described. A position whose quote was not found in the
          source is evidence about our extraction, not about this tender. */}
      {excluded.not_found > 0 && (
        <p className="muted small">
          {t('noticeExperts.withheld', { count: excluded.not_found })}
        </p>
      )}
    </section>
  );
}
