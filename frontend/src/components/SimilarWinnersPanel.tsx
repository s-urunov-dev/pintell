import { Link } from 'react-router-dom';

import { fetchSimilarAwards } from '../api/client';
import type {
  AwardParticipant,
  SearchResult,
  SimilarAwardRow,
  SimilarAwards,
} from '../api/types';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';
import { categoryShort, subcategoryShort } from '../lib/categories';
import { useCitation } from './CitationDock';

interface SimilarWinnersPanelProps {
  noticeId: string;
}

/**
 * What has already been awarded in this tender's line of work.
 *
 * The panel exists for one moment: a vendor is on an open tender deciding
 * whether to bid, and the archive knows who took the comparable work and at
 * what price. Nothing here is advice — it is the record, next to the tender.
 *
 * Three things it deliberately does not do:
 *
 * * **It ranks, and it shows its working.** Rows come back by cosine
 *   similarity over the semantic index (D45), not by a category filter. That
 *   is a number no reader can check, so the row leads with `match_passage` —
 *   the sentence the match was made on — and the score is a quiet figure
 *   beside it. Showing the score without the sentence would put back exactly
 *   the unaccountable ranking D42 removed.
 * * **A passage is a citation, not a caption.** Pressing it opens the sentence
 *   in the notice it came from, the same guarantee every other citation in
 *   this product carries.
 * * **It does not fill space when it has nothing.** An empty result is the
 *   normal answer for a tender whose line of work the classifier could not
 *   place, and showing the least-bad rows in the archive would be worse.
 *
 * Like `NoticeExpertsPanel`, it degrades to absence: this sits beside the real
 * content of the page, and an aside that announces its own failure costs the
 * reader more than it gives them.
 */
export default function SimilarWinnersPanel({ noticeId }: SimilarWinnersPanelProps) {
  const { t } = useI18n();
  const { open: openCitation } = useCitation();
  const similar = useAsyncData<SimilarAwards>(
    (signal) => fetchSimilarAwards(noticeId, signal),
    [noticeId],
  );

  if (similar.loading || similar.error != null || similar.data == null) return null;
  if (similar.data.results.length === 0) return null;

  return (
    <section className="card similar-winners">
      <h2 className="section-title">{t('similar.title')}</h2>
      <p className="muted small">{t('similar.lead')}</p>

      <ul className="similar-list">
        {similar.data.results.map((award) => (
          <SimilarRow key={award.notice_id} award={award} onCite={openCitation} />
        ))}
      </ul>

      <p className="muted small similar-note">{t('similar.note')}</p>

    </section>
  );
}

/**
 * The row's match, in the shape the citation viewer reads.
 *
 * Built here rather than sent as a second object by the server: everything it
 * needs is already on the row, and a parallel payload would be a second thing
 * to keep in step with `match_passage`.
 *
 * The passage is a notice-body sentence, so the viewer opens in text mode and
 * fetches the canonical source itself. No offsets are claimed — the awards
 * endpoint carries the sentence, not its position, and inventing a
 * `char_start` here would put a highlight on a guess.
 */
function asCitation(award: SimilarAwardRow): SearchResult {
  return {
    score: award.match_score,
    retrieval: 'vector',
    content: award.match_passage,
    notice_id: award.notice_id,
    title: award.title,
    source_type: 'text',
    payload: {
      source_key: `notice:${award.notice_id}`,
      notice_id: award.notice_id,
      category: award.category,
      subcategory: award.subcategory,
      document_id: '',
      title: award.title,
      source_type: 'text',
      position_id: '',
    },
  };
}

function SimilarRow({
  award,
  onCite,
}: {
  award: SimilarAwardRow;
  onCite: (result: SearchResult) => void;
}) {
  const { t, tv, formatDate, formatMoney } = useI18n();

  // The winner leads the row; the others are what made this a competition.
  const winners = award.participants.filter((p) => p.role === 'awardee');
  const others = award.participants.filter((p) => p.role !== 'awardee');

  return (
    <li className="similar-item">
      <div className="similar-head">
        <div>
          <p className="similar-companies">
            {winners.map((winner, index) => (
              <span key={`${winner.name}-${index}`}>
                {index > 0 && <span className="muted"> + </span>}
                <CompanyName participant={winner} />
              </span>
            ))}
            {winners.length === 0 && <span className="muted">{t('similar.noWinner')}</span>}
          </p>
          <p className="similar-title">{award.title}</p>
        </div>

        <div className="similar-figures">
          {award.contract_price && award.currency ? (
            <strong>{formatMoney(Number(award.contract_price), award.currency)}</strong>
          ) : null}
          <span className="muted small">{formatDate(award.award_date)}</span>
        </div>
      </div>

      {/* The sentence the match was made on, and the control that opens it in
          its own notice. It leads the metadata rather than trailing it because
          it is the only part of this row a reader can actually judge — the
          score below is not. */}
      {award.match_passage && (
        <button
          type="button"
          className="similar-passage"
          onClick={() => onCite(asCitation(award))}
          title={t('similar.openPassage')}
        >
          “{award.match_passage.slice(0, 200)}
          {award.match_passage.length > 200 ? '…' : ''}”
        </button>
      )}

      {/* Country and line of work, as plain facts about the contract. The
          sub-direction is only shown when there is one — it exists inside
          Consulting and nowhere else, so printing an empty tag for a supply
          award would suggest a missing value rather than an absent concept. */}
      <div className="similar-meta">
        {typeof award.match_score === 'number' && (
          <span className="tag tag-quiet" title={t('similar.scoreHint')}>
            {award.match_score.toFixed(2)}
          </span>
        )}
        <span className="tag tag-quiet">{tv('country', award.country)}</span>
        <span className="tag tag-quiet">{categoryShort(award.category, t)}</span>
        {subcategoryShort(award.subcategory, t) && (
          <span className="tag tag-quiet">{subcategoryShort(award.subcategory, t)}</span>
        )}
      </div>

      {others.length > 0 && (
        <p className="similar-others muted small">
          {t('similar.alsoBid')}{' '}
          {others.map((participant, index) => (
            <span key={`${participant.name}-${index}`}>
              {index > 0 && ', '}
              {participant.name}
              <span className="similar-role">
                {' '}
                {t(`similar.role.${participant.role}` as never)}
              </span>
            </span>
          ))}
        </p>
      )}

      <div className="similar-links">
        <Link to={`/tenders/${encodeURIComponent(award.notice_id)}`}>
          {t('similar.openAward')}
        </Link>
        <a href={award.source_url} target="_blank" rel="noopener noreferrer">
          {t('similar.openUpstream')}
        </a>
      </div>
    </li>
  );
}

/** The company, linked to its own site when one has been found for it. */
function CompanyName({ participant }: { participant: AwardParticipant }) {
  if (!participant.website) return <strong>{participant.name}</strong>;
  return (
    <a href={participant.website} target="_blank" rel="noopener noreferrer">
      <strong>{participant.name}</strong>
    </a>
  );
}
