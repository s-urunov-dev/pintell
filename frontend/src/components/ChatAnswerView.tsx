import { Link } from 'react-router-dom';

import type { ChatAnswer, ChatClaim, SearchResult } from '../api/types';
import { useI18n } from '../i18n';

/**
 * One answer, rendered as claims that each carry their own citations.
 *
 * Extracted from the widget when the chat gained a page of its own, and shared
 * rather than copied for a reason that is not tidiness: this component *is*
 * the product's guarantee made visible. A claim appears with a badge because
 * the server validated that the badge points at a passage the model was
 * actually shown, and a second copy of this file is a second place for that
 * rule to drift — one where a well-meaning edit could render a claim without
 * its sources and nobody would notice.
 *
 * It renders live answers and stored ones identically, because a stored answer
 * carries its own sources: the badge on a month-old reply opens the same
 * passage it opened the day it was written.
 */
export default function ChatAnswerView({
  answer,
  onCite,
  draft = '',
  incomplete = false,
}: {
  answer: ChatAnswer;
  onCite: (result: SearchResult) => void;
  /**
   * The sentence currently being written, with no citations yet.
   *
   * Rendered in its own row and marked as unfinished, never mixed in with the
   * claims: a claim carries the badges that make it checkable, and a line
   * without them must not be able to pass for one.
   */
  draft?: string;
  /** True while the model is still writing this answer. */
  incomplete?: boolean;
}) {
  const { t } = useI18n();

  // Nothing yet, and nothing to say about it — the waiting state belongs to the
  // page, not to an empty answer.
  if (incomplete && answer.claims.length === 0 && !draft) return null;

  if (answer.claims.length === 0 && !incomplete) {
    // No claims but passages: the model could not or would not answer, and the
    // sentences are still worth showing. Passages beat an apology.
    return (
      <div className="chat-answer">
        <p className="chat-note">
          {answer.sources.length > 0 ? t('chat.noAnswerButSources') : t('chat.nothingFound')}
        </p>
        {answer.degraded_reason && <p className="chat-note">{degradedText(answer, t)}</p>}
        <SourceList sources={answer.sources} onCite={onCite} />
      </div>
    );
  }

  return (
    <div className="chat-answer">
      {answer.retrieval === 'fts' && <p className="chat-note">{t('chat.keywordAnswer')}</p>}

      <ul className="chat-claims">
        {answer.claims.map((claim, index) => (
          <ClaimView key={index} claim={claim} sources={answer.sources} onCite={onCite} />
        ))}
        {/* The sentence being written. It grows word by word and carries no
            badge, because its citations have not been written yet — the
            checked claim replaces it in place the moment it closes. */}
        {draft && (
          <li className="chat-claim chat-claim-draft">
            <span>{draft}</span>
            <span className="chat-caret" aria-hidden="true" />
          </li>
        )}
      </ul>

      {/* Shown, not hidden. A model that produced a sentence it could not back
          is a fact about this answer, and burying it is how the number stops
          being watched. */}
      {answer.unsupported > 0 && (
        <p className="chat-note chat-dropped">
          {t('chat.dropped', { count: answer.unsupported })}
        </p>
      )}
    </div>
  );
}

function ClaimView({
  claim,
  sources,
  onCite,
}: {
  claim: ChatClaim;
  sources: SearchResult[];
  onCite: (result: SearchResult) => void;
}) {
  return (
    <li className="chat-claim">
      <span>{claim.text}</span>{' '}
      {claim.sources.map((index) => {
        const source = sources[index];
        if (!source) return null;
        return <CiteBadge key={index} source={source} number={index + 1} onCite={onCite} />;
      })}
    </li>
  );
}

/**
 * One citation number, and the two things it can be.
 *
 * Most sources are passages, and pressing one opens the document beside the
 * answer. **A `record` source is not a document**: it is a row of our own
 * tender tables, added because retrieval cannot answer "what is open right
 * now" and the alternative — an uncitable context block — had the model citing
 * a passage for facts it took from somewhere else (see
 * `rag_indexer.services.chat.record_sources`). There is nothing for the viewer
 * to open, so the honest control is a link: to the tender when the row is one
 * notice, and to the list when it is the open set.
 *
 * That much was already true. What was wrong is that it was drawn as the badge
 * beside it, so a reader pressing it expected a pane and lost the page instead
 * — and as a bare `<a>` it reloaded the whole app on the way out. Now it is
 * outlined rather than filled, it says where it goes, and it navigates like
 * every other link in the product.
 */
function CiteBadge({
  source,
  number,
  onCite,
}: {
  source: SearchResult;
  number: number;
  onCite: (result: SearchResult) => void;
}) {
  const { t } = useI18n();
  const label = source.payload.title || source.notice_id;

  if (source.source_type === 'record') {
    const toTender = Boolean(source.notice_id);
    const explains = t(toTender ? 'chat.citeRecord' : 'chat.citeRecordList');
    return (
      <Link
        className="chat-cite chat-cite-record"
        to={toTender ? `/tenders/${encodeURIComponent(source.notice_id)}` : '/search'}
        title={label ? `${label} — ${explains}` : explains}
        aria-label={explains}
      >
        {number}
      </Link>
    );
  }

  return (
    <button
      type="button"
      className="chat-cite"
      onClick={() => onCite(source)}
      title={label}
      aria-label={t('chat.openSource')}
    >
      {number}
    </button>
  );
}

export function SourceList({
  sources,
  onCite,
}: {
  sources: SearchResult[];
  onCite: (result: SearchResult) => void;
}) {
  if (sources.length === 0) return null;
  return (
    <ol className="chat-sources">
      {sources.slice(0, 4).map((source, index) => (
        <li key={index}>
          {/* The same badge as in a claim, and for the same reason: a row of
              our own tables has no pane to open here either. */}
          <CiteBadge source={source} number={index + 1} onCite={onCite} />{' '}
          <span className="muted small">{source.content.slice(0, 120)}…</span>
        </li>
      ))}
    </ol>
  );
}

/** Why the answer is weaker than it could be, in the reader's language. */
export function degradedText(
  answer: Pick<ChatAnswer, 'degraded_reason'>,
  t: ReturnType<typeof useI18n>['t'],
): string {
  switch (answer.degraded_reason) {
    case 'model_unavailable':
      return t('chat.degraded.model');
    case 'model_failed':
      return t('chat.degraded.failed');
    case 'embeddings_unavailable':
    case 'vector_store_unavailable':
      return t('chat.degraded.index');
    default:
      return '';
  }
}
