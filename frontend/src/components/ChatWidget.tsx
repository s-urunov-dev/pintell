import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { askArchive } from '../api/client';
import type { ChatAnswer, SearchResult } from '../api/types';
import { useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';
import ChatAnswerView from './ChatAnswerView';
import { useCitation } from './CitationDock';

/**
 * Asking the archive a question in words, with every sentence traceable.
 *
 * The difference between this and a chatbot bolted onto a product is one
 * property, and it is enforced on the server rather than requested in a
 * prompt: **a claim reaches this component only if it cites a passage that was
 * actually retrieved**. The model is shown a numbered list and can answer only
 * with indices into it; an index that does not exist is discarded, and a claim
 * left with none is dropped and counted. So the badge beside a sentence is
 * never decorative — it always opens a real passage in a real document.
 *
 * That is why the answer renders as a list of claims rather than a paragraph.
 * A block of prose would force this file to hunt for citation markers in text,
 * and the marker would be a string the model wrote rather than a checked
 * reference. Here the structure carries the guarantee.
 *
 * **It says how it answered.** When the semantic index is unavailable the
 * server falls back to Postgres keyword matching, and the panel says so — the
 * sentences are then written over weaker material and the reader should know
 * before acting on them.
 *
 * A floating widget rather than a page, because the question is almost always
 * asked *about something on screen*: opened from a tender it scopes itself to
 * that tender, which is what `noticeId` is for.
 */
export default function ChatWidget({ noticeId }: { noticeId?: string }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Closed on navigation. A panel that follows the reader onto another page
  // still holds an answer about the page they left, and its citations open
  // documents that are no longer what they are looking at.
  useEffect(() => setOpen(false), [location.pathname]);

  return (
    <>
      <button
        type="button"
        className="chat-launcher"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={t('chat.title')}
      >
        {open ? '×' : '💬'}
      </button>
      {open && <ChatPanel noticeId={noticeId} onClose={() => setOpen(false)} />}
    </>
  );
}

type Turn = {
  question: string;
  answer: ChatAnswer | null;
  error: unknown;
};

function ChatPanel({
  noticeId,
  onClose,
}: {
  noticeId?: string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  // The passage opens in the shell's own column, so it is readable beside
  // the page rather than inside this panel, which has no room for it.
  const { open: openCitation } = useCitation();
  const logRef = useRef<HTMLDivElement | null>(null);
  // Held in a ref rather than state: it changes once, on the first answer, and
  // nothing on screen reads it — re-rendering the panel for it would be a
  // render for the benefit of no pixel.
  const conversationRef = useRef<string | undefined>(undefined);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => inputRef.current?.focus(), []);

  // Keep the newest turn in view as answers arrive.
  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [turns, busy]);

  const ask = useCallback(
    async (question: string) => {
      // The widget keeps its thread like the page does, so a follow-up asked
      // from a tender continues the same conversation and is found again in
      // the sidebar afterwards. The guarantee is untouched by that: earlier
      // turns tell the model what the question refers to, and every claim it
      // writes must still cite a passage retrieved for *this* question.
      setBusy(true);
      setTurns((previous) => [...previous, { question, answer: null, error: null }]);
      try {
        const answer = await askArchive(question, {
          noticeId,
          conversationId: conversationRef.current,
        });
        if (answer.conversation_id) conversationRef.current = answer.conversation_id;
        setTurns((previous) =>
          previous.map((turn, index) =>
            index === previous.length - 1 ? { ...turn, answer } : turn,
          ),
        );
      } catch (error: unknown) {
        setTurns((previous) =>
          previous.map((turn, index) =>
            index === previous.length - 1 ? { ...turn, error } : turn,
          ),
        );
      } finally {
        setBusy(false);
      }
    },
    [noticeId],
  );

  return (
    <div className="chat-panel" role="dialog" aria-label={t('chat.title')}>
      <header className="chat-head">
        <div>
          <h2>{t('chat.title')}</h2>
          <p className="muted small">
            {noticeId ? t('chat.scopedToTender') : t('chat.scopedToArchive')}
          </p>
        </div>
        <div className="chat-head-actions">
          {/* The same conversation, on a page with room for it. `state` carries
              the thread so the page opens where the panel left off rather than
              starting a second one about the same tender. */}
          <Link
            className="btn btn-ghost btn-small"
            to="/chat"
            state={{ conversationId: conversationRef.current, noticeId }}
            onClick={onClose}
          >
            {t('chat.openFull')}
          </Link>
          <button type="button" className="citation-close" onClick={onClose} aria-label={t('chat.close')}>
            ×
          </button>
        </div>
      </header>

      <div className="chat-log" ref={logRef}>
        {turns.length === 0 && (
          <div className="chat-empty">
            <p className="muted">{t('chat.intro')}</p>
            <ul className="chat-suggestions">
              {['chat.example1', 'chat.example2', 'chat.example3'].map((key) => (
                <li key={key}>
                  <button type="button" onClick={() => ask(t(key as 'chat.example1'))}>
                    {t(key as 'chat.example1')}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {turns.map((turn, index) => (
          <TurnView key={index} turn={turn} onCite={openCitation} />
        ))}

        {busy && <p className="chat-thinking">{t('chat.thinking')}</p>}
      </div>

      <form
        className="chat-form"
        onSubmit={(event) => {
          event.preventDefault();
          const question = draft.trim();
          if (!question || busy) return;
          setDraft('');
          void ask(question);
        }}
      >
        <input
          ref={inputRef}
          type="text"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t('chat.placeholder')}
          aria-label={t('chat.title')}
          maxLength={1000}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          {t('chat.send')}
        </button>
      </form>

    </div>
  );
}

function TurnView({
  turn,
  onCite,
}: {
  turn: Turn;
  onCite: (result: SearchResult) => void;
}) {
  const { t } = useI18n();

  return (
    <div className="chat-turn">
      <p className="chat-question">{turn.question}</p>

      {turn.error ? (
        <p className="chat-error">{errorMessage(turn.error, t)}</p>
      ) : turn.answer ? (
        <ChatAnswerView answer={turn.answer} onCite={onCite} />
      ) : null}
    </div>
  );
}
