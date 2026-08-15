import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';

import {
  askArchive,
  askArchiveStreaming,
  deleteConversation,
  getConversation,
  listConversations,
  renameConversation,
} from '../api/client';
import type {
  ChatAnswer,
  ChatClaim,
  ChatStage,
  Conversation,
  ConversationMessage,
  SearchResult,
} from '../api/types';
import ChatAnswerView from '../components/ChatAnswerView';
import { useCitation } from '../components/CitationDock';
import { useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

/**
 * The archive, asked in a conversation that is kept.
 *
 * A page rather than the floating widget, because a thread has things a panel
 * cannot hold: earlier questions worth rereading, a sidebar of what was asked
 * before, and room for an answer that runs to a dozen claims. The widget stays
 * for the question asked *about the notice on screen* and hands its thread over
 * here when the reader wants the space.
 *
 * **Two properties survive the redesign, and they are the point of it.** A
 * claim is only shown with the citations the server validated, and the renderer
 * is shared with the widget (`ChatAnswerView`) so it cannot drift. A *stored*
 * answer renders identically to a fresh one, because the turn was saved with
 * its own passages.
 *
 * **One turn is one element from the first token to the last.** The answer is
 * built up in place: the passages arrive before the model writes, each sentence
 * is drawn as it is written, and the final validated answer lands on the same
 * object under the same key. Nothing is unmounted and re-mounted, so the
 * finished answer does not flash or re-flow — which is exactly what happens
 * when a "live" bubble is swapped for a "real" one at the end.
 *
 * **Motion is reserved for what has just happened.** Only turns that arrived
 * while the reader was watching animate; opening a saved thread puts it at the
 * bottom immediately, with no entrance to sit through for text already read.
 */
export default function ChatPage() {
  const { t } = useI18n();
  const location = useLocation();
  const handover = (location.state ?? {}) as { conversationId?: string; noticeId?: string };

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | undefined>(handover.conversationId);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [stage, setStage] = useState<ChatStage | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [olderPending, setOlderPending] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  // Opened in the shell, beside the page, so the answer and the passage it
  // cites are read side by side rather than one over the other.
  const { open: openCitation } = useCitation();
  const [draft, setDraft] = useState('');
  /**
   * Whether the rail of past conversations is showing.
   *
   * One state for two layouts: beside the thread when there is room for two
   * columns, over it when there is not. What differs is only where it starts —
   * a column is worth having open, a drawer across what you came to read is
   * not.
   */
  const [railOpen, setRailOpen] = useState(() => !isNarrow());
  /**
   * Whether the rail has to be a drawer, asked of **the column this page has**
   * rather than of the window.
   *
   * A source docked beside the chat (D51) takes its width from this page, and
   * the window has not changed size — so a media query cannot see the one case
   * that actually hurts: the rail keeping 272px of a column that no longer has
   * them, and the thread reduced to a strip of three-word lines. Same reasoning
   * as the notice page's container query (D52), in JavaScript because unlike
   * that page this one has to *close* the rail, not just re-lay it, and a
   * stylesheet cannot tell the toggle what it did.
   */
  const [drawer, setDrawer] = useState(isNarrow);
  const pageRef = useRef<HTMLDivElement | null>(null);
  const wasDrawer = useRef(isNarrow());
  /**
   * What the reader last chose while there was room for a column.
   *
   * Restored when the room comes back, so closing the source gives back the
   * rail they had — and does not give back one they had deliberately folded.
   */
  const preferred = useRef(!isNarrow());

  useEffect(() => {
    const element = pageRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const next = entry.contentRect.width < TWO_COLUMN_WIDTH;
      // Only on the crossing. Firing on every observed frame would re-close a
      // rail the reader had just opened, on nothing more than a scrollbar
      // appearing.
      if (next === wasDrawer.current) return;
      wasDrawer.current = next;
      setDrawer(next);
      setRailOpen(next ? false : preferred.current);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const toggleRail = useCallback(() => {
    setRailOpen((open) => {
      // A choice made in drawer mode is about this moment, not a preference:
      // the drawer is always closed on arrival, so remembering "open" would
      // reopen it over the thread the next time the window grew.
      if (!wasDrawer.current) preferred.current = !open;
      return !open;
    });
  }, []);

  const threadRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const noticeRef = useRef<string | undefined>(handover.noticeId);
  // Rising counter behind the keys of turns this browser created. A stable key
  // is what lets an answer be filled in rather than replaced.
  const localKey = useRef(0);
  // How the next scroll should behave. Opening a thread jumps; a turn arriving
  // while the reader watches glides.
  const jump = useRef(true);
  // Scroll height captured before older turns are prepended, so the view can be
  // put back where the reader left it.
  const anchor = useRef<number | null>(null);
  // The latest turns, readable from inside `ask` without making it depend on
  // them — a dependency there would rebuild the callback on every token.
  const current = useRef<Turn[]>(turns);
  current.current = turns;
  // Threads this page already holds in full. A brand-new conversation is one
  // of them: its id arrives with the answer, and re-fetching a thread whose
  // only two turns are already on screen would remount them for nothing.
  const local = useRef<Set<string>>(new Set());

  const refreshList = useCallback(async () => {
    try {
      const response = await listConversations();
      setConversations(response.results);
    } catch {
      // A sidebar that could not load is a sidebar. The composer still works,
      // and a failed list must never stand between a reader and a question.
    }
  }, []);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  // Open the thread the reader selected — or the one handed over by the widget.
  useEffect(() => {
    if (!activeId) {
      setTurns([]);
      setHasMore(false);
      return;
    }
    if (local.current.has(activeId)) return;

    let cancelled = false;
    setLoadingThread(true);
    jump.current = true;
    getConversation(activeId)
      .then((response) => {
        if (cancelled) return;
        setTurns(response.messages.map(fromStored));
        setHasMore(response.has_more);
        noticeRef.current = response.conversation.notice_id || undefined;
      })
      .catch(() => {
        // A thread that will not open (deleted in another tab, or expired with
        // the session) becomes a new one rather than an error page.
        if (!cancelled) {
          setActiveId(undefined);
          setTurns([]);
        }
      })
      .finally(() => !cancelled && setLoadingThread(false));
    return () => {
      cancelled = true;
    };
  }, [activeId]);

  // Keep the newest turn in view. Laid out first, then scrolled: a plain effect
  // runs before the browser has placed the new text, so scrolling to
  // `scrollHeight` there goes to the height the thread had a moment ago.
  useLayoutEffect(() => {
    const thread = threadRef.current;
    if (!thread) return;

    // Older turns were just prepended: hold the reader's place rather than
    // moving them. Without this, loading history scrolls them to whatever now
    // sits at the old offset.
    if (anchor.current !== null) {
      thread.scrollTop += thread.scrollHeight - anchor.current;
      anchor.current = null;
      return;
    }

    const behavior = jump.current ? 'auto' : 'smooth';
    jump.current = false;
    const frame = requestAnimationFrame(() => {
      thread.scrollTo({ top: thread.scrollHeight, behavior });
    });
    return () => cancelAnimationFrame(frame);
  }, [turns, stage]);

  useEffect(() => inputRef.current?.focus(), [activeId]);

  const loadOlder = useCallback(async () => {
    const oldest = current.current.find((turn) => turn.id > 0);
    if (!activeId || !hasMore || olderPending || !oldest) return;
    setOlderPending(true);
    try {
      const response = await getConversation(activeId, { before: oldest.id });
      anchor.current = threadRef.current?.scrollHeight ?? null;
      setTurns((previous) => [...response.messages.map(fromStored), ...previous]);
      setHasMore(response.has_more);
    } catch {
      // Nothing to do but let the reader try again by scrolling.
    } finally {
      setOlderPending(false);
    }
  }, [activeId, hasMore, olderPending]);

  const onScroll = useCallback(() => {
    const thread = threadRef.current;
    // A screen's warning, so the fetch finishes before the reader reaches the
    // top and the thread never visibly stops.
    if (thread && thread.scrollTop < 400) void loadOlder();
  }, [loadOlder]);

  const ask = useCallback(
    async (question: string) => {
      setBusy(true);
      setStage({ stage: 'retrieving' });

      const questionKey = `local-${(localKey.current += 1)}`;
      const answerKey = `local-${(localKey.current += 1)}`;
      setTurns((previous) => [
        ...previous,
        { ...blank(questionKey, 'user'), text: question, fresh: true },
        { ...blank(answerKey, 'assistant'), pending: true, fresh: true },
      ]);

      // Every update below finds the answer turn by its key and rewrites that
      // one object. The element on screen is never replaced, which is why the
      // finished answer does not flash.
      const patch = (change: Partial<Turn>) =>
        setTurns((previous) =>
          previous.map((turn) => (turn.key === answerKey ? { ...turn, ...change } : turn)),
        );
      const claimsSoFar = () =>
        current.current.find((turn) => turn.key === answerKey)?.claims ?? [];

      const options = { noticeId: noticeRef.current, conversationId: activeId };
      try {
        let answer: ChatAnswer;
        try {
          answer = await askArchiveStreaming(
            question,
            options,
            setStage,
            (claim) =>
              patch({
                draft: '',
                claims: replaceAt(claimsSoFar(), claim.index, {
                  text: claim.text,
                  sources: claim.sources,
                }),
              }),
            (line) => patch({ draft: line.text }),
            (sources) => patch({ sources }),
          );
        } catch (streamError: unknown) {
          if (streamError instanceof DOMException && streamError.name === 'AbortError') {
            throw streamError;
          }
          // The narration failed, not the product. Ask again the plain way
          // rather than telling a reader their question could not be answered
          // because the progress report could not be drawn.
          setStage(null);
          answer = await askArchive(question, options);
        }

        if (answer.conversation_id) {
          local.current.add(answer.conversation_id);
          setActiveId(answer.conversation_id);
        }
        patch({ ...fromAnswer(answer), pending: false, draft: '' });
        void refreshList();
      } catch (error: unknown) {
        patch({ pending: false, draft: '', error });
      } finally {
        setBusy(false);
        setStage(null);
      }
    },
    [activeId, refreshList],
  );

  const startNew = useCallback(() => {
    setActiveId(undefined);
    setTurns([]);
    setHasMore(false);
    noticeRef.current = undefined;
    // Same rule as picking a thread: the drawer is in the way of the empty
    // composer, the column is not.
    if (wasDrawer.current) setRailOpen(false);
    inputRef.current?.focus();
  }, []);

  const remove = useCallback(
    async (id: string) => {
      try {
        await deleteConversation(id);
      } catch {
        return;
      }
      setConversations((previous) => previous.filter((row) => row.id !== id));
      if (id === activeId) startNew();
    },
    [activeId, startNew],
  );

  const rename = useCallback(async (id: string, title: string) => {
    const trimmed = title.trim();
    if (!trimmed) return;
    try {
      const updated = await renameConversation(id, trimmed);
      setConversations((previous) =>
        previous.map((row) => (row.id === id ? { ...row, title: updated.title } : row)),
      );
    } catch {
      // Keeping the old name is the whole cost of a failed rename.
    }
  }, []);

  const send = () => {
    const question = draft.trim();
    if (!question || busy) return;
    setDraft('');
    void ask(question);
  };

  const empty = turns.length === 0;

  return (
    <div
      ref={pageRef}
      className={`chat-page${railOpen ? ' chat-page-open' : ''}${
        drawer ? ' chat-page-drawer' : ''
      }`}
    >
      {/* `inert` and not just `hidden`: collapsed, the rail is still in the
          layout with its width animated to zero, and a clipped column that
          still takes tab stops is a keyboard trap you cannot see. */}
      <aside className="chat-rail" id="chat-rail" inert={!railOpen}>
        <button type="button" className="chat-new" onClick={startNew}>
          <span aria-hidden="true">＋</span> {t('chat.newThread')}
        </button>

        <div className="chat-rail-list">
          {conversations.length === 0 && (
            <p className="muted small chat-rail-empty">{t('chat.noThreads')}</p>
          )}
          {conversations.map((conversation) => (
            <ThreadRow
              key={conversation.id}
              conversation={conversation}
              active={conversation.id === activeId}
              onOpen={() => {
                setActiveId(conversation.id);
                // Only where the rail is covering the thread. Beside it, on a
                // page with room for both, closing it after every pick would
                // take away the list the reader is browsing.
                if (wasDrawer.current) setRailOpen(false);
              }}
              onRename={(title) => void rename(conversation.id, title)}
              onDelete={() => void remove(conversation.id)}
            />
          ))}
        </div>
      </aside>

      <section className="chat-main">
        <header className="chat-main-head">
          <button
            type="button"
            className="btn btn-ghost btn-small chat-rail-toggle"
            onClick={toggleRail}
            aria-expanded={railOpen}
            aria-controls="chat-rail"
            aria-label={t(railOpen ? 'chat.hideThreads' : 'chat.showThreads')}
            title={t(railOpen ? 'chat.hideThreads' : 'chat.showThreads')}
          >
            <span className="chat-rail-toggle-icon" aria-hidden="true" />
            {/* Dropped to the icon alone where the head has no room for it —
                the button keeps its name for a screen reader either way. */}
            <span className="chat-rail-toggle-label">{t('chat.threads')}</span>
          </button>
          <div>
            <h1>{t('chat.pageTitle')}</h1>
            <p className="muted small">
              {noticeRef.current ? t('chat.scopedToTender') : t('chat.scopedToArchive')}
            </p>
          </div>
        </header>

        <div className="chat-thread" ref={threadRef} onScroll={onScroll}>
          {loadingThread && <div className="chat-skeleton" aria-hidden="true" />}
          {olderPending && <p className="chat-older muted small">{t('chat.loadingOlder')}</p>}

          {empty && !loadingThread && (
            <div className="chat-welcome">
              <h2>{t('chat.welcomeTitle')}</h2>
              <p className="muted">{t('chat.intro')}</p>
              <div className="chat-starters">
                {['chat.example1', 'chat.example2', 'chat.example3'].map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => void ask(t(key as 'chat.example1'))}
                  >
                    {t(key as 'chat.example1')}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((turn) =>
            turn.role === 'user' ? (
              <div
                key={turn.key}
                className={`chat-bubble chat-bubble-user${turn.fresh ? ' chat-enter' : ''}`}
              >
                {turn.text}
              </div>
            ) : (
              <div
                key={turn.key}
                className={`chat-bubble chat-bubble-answer${turn.fresh ? ' chat-enter' : ''}`}
              >
                {turn.error ? (
                  <p className="chat-error">{errorMessage(turn.error, t)}</p>
                ) : (
                  <ChatAnswerView
                    answer={toAnswer(turn)}
                    onCite={openCitation}
                    draft={turn.draft}
                    incomplete={turn.pending}
                  />
                )}
              </div>
            ),
          )}

          {busy && <ThinkingView stage={stage} />}
        </div>

        <form
          className="chat-composer"
          onSubmit={(event) => {
            event.preventDefault();
            send();
          }}
        >
          <textarea
            ref={inputRef}
            value={draft}
            rows={1}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends, Shift+Enter breaks the line — the convention every
              // reader already has in their fingers.
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
            placeholder={t('chat.placeholder')}
            aria-label={t('chat.pageTitle')}
            maxLength={1000}
          />
          <button type="submit" className="chat-send" disabled={busy || !draft.trim()}>
            {busy ? <span className="chat-send-spinner" aria-hidden="true" /> : t('chat.send')}
          </button>
        </form>
        <p className="chat-footnote muted small">{t('chat.grounding')}</p>
      </section>

    </div>
  );
}

/**
 * One turn on screen: a stored message, or one still being written.
 *
 * Stored and live turns share a shape so that the answer arriving changes the
 * fields on a turn rather than the element rendering it.
 */
type Turn = {
  key: string;
  id: number;
  role: 'user' | 'assistant';
  text: string;
  claims: ChatClaim[];
  sources: SearchResult[];
  retrieval: ChatAnswer['retrieval'];
  degraded_reason: string;
  unsupported: number;
  /** The sentence being written, with no citations yet. */
  draft?: string;
  /** True while the model is still writing this turn. */
  pending?: boolean;
  /** True only for turns that arrived while the reader was watching. */
  fresh?: boolean;
  error?: unknown;
};

/**
 * The narrowest page that can still hold the rail *and* a thread.
 *
 * 272px of rail, the gap, and about 520px of thread — below which an answer is
 * a column of three-word lines and the list beside it is the reason. It is one
 * number for two cases that used to be two: a small window, and a wide window
 * sharing this page with a docked source.
 */
const TWO_COLUMN_WIDTH = 820;

/**
 * The same question asked of the window, for the first render.
 *
 * The observer answers it properly a frame later from the page's own width;
 * this only keeps a narrow phone from painting the rail once before it does.
 */
function isNarrow(): boolean {
  return window.matchMedia('(max-width: 860px)').matches;
}

function blank(key: string, role: 'user' | 'assistant'): Turn {
  return {
    key,
    id: 0,
    role,
    text: '',
    claims: [],
    sources: [],
    retrieval: 'none',
    degraded_reason: '',
    unsupported: 0,
  };
}

/** A stored turn. Never `fresh`: the reader has seen it before. */
function fromStored(message: ConversationMessage): Turn {
  return {
    key: `m-${message.id}`,
    id: message.id,
    role: message.role,
    text: message.text,
    claims: message.claims ?? [],
    sources: message.sources ?? [],
    retrieval: (message.retrieval || 'none') as ChatAnswer['retrieval'],
    degraded_reason: message.degraded_reason,
    unsupported: message.unsupported,
  };
}

/** The fields a finished answer contributes to the turn it was written into. */
function fromAnswer(answer: ChatAnswer): Partial<Turn> {
  return {
    id: answer.message_id ?? 0,
    text: answer.claims.map((claim) => claim.text).join(' '),
    claims: answer.claims,
    sources: answer.sources,
    retrieval: answer.retrieval,
    degraded_reason: answer.degraded_reason,
    unsupported: answer.unsupported,
  };
}

function toAnswer(turn: Turn): ChatAnswer {
  return {
    claims: turn.claims,
    sources: turn.sources,
    retrieval: turn.retrieval,
    degraded_reason: turn.degraded_reason,
    unsupported: turn.unsupported,
    took_ms: 0,
    prompt_version: '',
  };
}

/** Put a claim at its own index, growing the list if it is the next one. */
function replaceAt(claims: ChatClaim[], index: number, claim: ChatClaim): ChatClaim[] {
  const next = [...claims];
  next[index] = claim;
  return next;
}

/**
 * What the reader is told while they wait.
 *
 * Each line corresponds to a stage the server actually reported. An unknown
 * stage falls back to the generic line rather than being hidden: the set can
 * grow server-side, and a silent gap in the narration reads as a stall.
 */
function ThinkingView({ stage }: { stage: ChatStage | null }) {
  const { t } = useI18n();

  const label = (() => {
    switch (stage?.stage) {
      case 'retrieving':
        return t('chat.stage.retrieving');
      case 'reading':
        return t('chat.stage.reading', { count: stage?.sources ?? 0 });
      case 'writing':
        return t('chat.stage.writing');
      default:
        return t('chat.thinking');
    }
  })();

  return (
    <div className="chat-thinking-card" role="status" aria-live="polite">
      <span className="chat-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className="chat-stage-label">{label}</span>
    </div>
  );
}

function ThreadRow({
  conversation,
  active,
  onOpen,
  onRename,
  onDelete,
}: {
  conversation: Conversation;
  active: boolean;
  onOpen: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(conversation.title);

  if (editing) {
    return (
      <form
        className="chat-rail-row chat-rail-editing"
        onSubmit={(event) => {
          event.preventDefault();
          onRename(value);
          setEditing(false);
        }}
      >
        <input
          autoFocus
          value={value}
          maxLength={80}
          onChange={(event) => setValue(event.target.value)}
          onBlur={() => {
            onRename(value);
            setEditing(false);
          }}
          aria-label={t('chat.rename')}
        />
      </form>
    );
  }

  return (
    <div className={active ? 'chat-rail-row chat-rail-active' : 'chat-rail-row'}>
      <button type="button" className="chat-rail-open" onClick={onOpen}>
        <span className="chat-rail-title">{conversation.title || t('chat.untitled')}</span>
        <span className="chat-rail-meta muted small">
          {conversation.notice_id ? conversation.notice_id : t('chat.wholeArchive')}
        </span>
      </button>
      <span className="chat-rail-tools">
        <button
          type="button"
          onClick={() => setEditing(true)}
          aria-label={t('chat.rename')}
          title={t('chat.rename')}
        >
          ✎
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label={t('chat.delete')}
          title={t('chat.delete')}
        >
          ×
        </button>
      </span>
    </div>
  );
}
