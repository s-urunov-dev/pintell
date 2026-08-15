import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import { Link } from 'react-router-dom';

import { documentFileUrl, fetchSourceText } from '../api/client';
import type { SearchResult, SourceText } from '../api/types';
import { useCitation } from './CitationDock';
import { useI18n } from '../i18n';
import { loadPdf, renderPage, type PdfDocument } from '../lib/pdf';

/**
 * A retrieved passage, shown where it actually sits in the source.
 *
 * A search result on its own is a paragraph with no provenance: it reads like
 * an answer, and a vendor deciding whether to spend three weeks on a proposal
 * cannot check it. Opening the citation is what turns it back into a quote —
 * the borrower's own page, laid out as the borrower laid it out, with the
 * retrieved lines boxed.
 *
 * **The highlight is never a new claim.** Where the result carries coordinates
 * they were computed when the passage was indexed, from the same parse the
 * page is rendered from, and they are used unchanged. Nothing here asks a
 * model where anything is.
 *
 * Some citations arrive with a sentence and no position — the similar-awards
 * rows do, because that endpoint answers with a passage rather than an offset.
 * Those are located here by **exact string match** against the canonical text,
 * the same rule `compliance.spans.Locator` follows on the server, and a miss
 * means no highlight rather than a near one. A fuzzy match would box a line
 * that does not say what the citation says, which is worse than no box because
 * it reads as proof.
 *
 * Two modes, chosen by the payload's own `source_type` and never by a
 * preference here:
 *
 * * **`pdf`** — the mirrored file, at the cited page, with the rectangle drawn
 *   over it. Only that page is rendered: a citation is a place, and paging a
 *   four-hundred-page bidding document into a modal to show one paragraph
 *   spends seconds of the reader's time to prove nothing extra.
 * * **`text`** — the notice body (or a document with no page geometry), with
 *   the cited character range marked.
 */
export default function CitationViewer({
  result,
  onClose,
}: {
  result: SearchResult;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const dockRef = useRef<HTMLDivElement | null>(null);
  const { width, resize } = useCitation();
  const { payload } = result;

  // Escape closes, and focus starts on the close button. The minimum a dialog
  // owes a keyboard: without it the modal is a trap that only a mouse leaves.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    closeRef.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const isPdf = payload.source_type === 'pdf' && Boolean(payload.document_id);

  return (
    <>
      {/* A docked panel, not a modal over the page.
          The source is read *against* the answer that cited it — "does this
          sentence really say that" is a comparison, and a dialog covering the
          claim makes the reader hold one half of it in their head. So the page
          keeps its place on the left and the document opens beside it, and
          nothing here traps focus or blocks the page behind. It becomes a
          full-height sheet only where there is no room to sit beside anything. */}
      {/* The seam between the two columns, and the control that moves it.
          Rendered before the pane so it sits between the page and the source
          in the flex order — the split it drags is the one it stands in. */}
      <DockResizer dockRef={dockRef} onResize={resize} />

      <div
        ref={dockRef}
        className="citation-dock"
        role="complementary"
        aria-label={t('search.citation.title')}
        /* The dragged width overrides the CSS default in place, so the
           keyframe and the rule keep reading one variable (D53). */
        style={width === null ? undefined : ({ '--dock-width': `${width}px` } as CSSProperties)}
      >
        <header className="citation-head">
          <div>
            <h2>{payload.title || result.notice_id}</h2>
            <p className="muted small">
              {t('search.citation.notice')}{' '}
              {/* A route change, not a page load: the shell keeps the theme,
                  the language and the thread, and closing the pane on arrival
                  is then something the app does rather than something the
                  reload happens to do. */}
              <Link to={`/tenders/${encodeURIComponent(result.notice_id)}`}>
                {result.notice_id}
              </Link>
              {isPdf && payload.page ? ` · ${t('search.citation.page')} ${payload.page}` : ''}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="citation-close"
            onClick={onClose}
            aria-label={t('search.citation.close')}
          >
            ×
          </button>
        </header>

        <div className="citation-body">
          {isPdf ? (
            <PdfCitation
              documentId={payload.document_id}
              page={payload.page ?? 1}
              bbox={payload.bbox ?? [0, 0, 0, 0]}
              pageWidth={payload.page_width ?? 612}
              pageHeight={payload.page_height ?? 792}
              fallback={result.content}
            />
          ) : (
            <TextCitation
              sourceKey={payload.source_key}
              charStart={payload.char_start ?? 0}
              charEnd={payload.char_end ?? 0}
              fallback={result.content}
            />
          )}
        </div>
      </div>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* The split between the page and the source                                   */
/* -------------------------------------------------------------------------- */
/** The narrowest useful source column, and the least page worth leaving. */
const MIN_DOCK = 320;
const MIN_PAGE = 360;
/** One arrow press. Coarse enough to be worth pressing, fine enough to aim. */
const KEY_STEP = 32;

/**
 * How wide the pane is allowed to be, whatever it was asked for.
 *
 * Measured against the window rather than against the split: they are the same
 * number here (the split spans the shell), and reading the window means the
 * clamp holds during a drag that started before the window was resized.
 *
 * These two bounds are the only ones. An earlier version also stopped the drag
 * at the width where the notice page's layout would change shape — that made
 * two thirds of the range unreachable to protect a layout, and it is the
 * layout's job to hold: the reading column is capped in CSS instead
 * (`.app-split-open .detail`), so the page keeps its shape at every width the
 * seam can be dragged to (D56).
 */
function clampDockWidth(px: number): number {
  const max = Math.max(MIN_DOCK, window.innerWidth - MIN_PAGE);
  return Math.min(Math.max(px, MIN_DOCK), max);
}

/**
 * The seam, as something the reader can take hold of.
 *
 * The split's width is a judgement only the reader can make — a table of bid
 * prices wants the room a paragraph does not — and until now it was a constant
 * in a stylesheet. Dragging changes one custom property; every consequence
 * follows from the rules that were already there, because the detail page
 * measures *its own column* (D52): drag the source narrow enough and the
 * notice's rail comes back on its own, with nothing here knowing that page
 * exists.
 *
 * Pointer events rather than mouse events, so a trackpad, a touch screen and a
 * pen are one code path.
 *
 * **The drag is run from `window`, and the width is written to the DOM until
 * the reader lets go.** Both halves of that are fixes for the same report — the
 * seam getting stuck to the cursor after the button was released, and moving in
 * lurches while it was held.
 *
 * *Stuck*: the first version tracked the gesture with `setPointerCapture` and
 * handlers on the strip itself, so ending it depended on that one element
 * seeing the `pointerup`. Anything that takes the capture away first — the
 * browser's own drag handling, a re-render, a release over a scrollbar or
 * outside the window — leaves `dragging` true with no event left that can
 * clear it, and the seam follows the pointer forever. Listeners on `window`
 * for the life of the gesture cannot be missed like that: they are added on
 * pointerdown, and the same teardown runs for `pointerup`, `pointercancel` and
 * a lost window focus.
 *
 * *Lurching*: the width lived in React state at the top of the app, so every
 * pointermove re-rendered the shell and everything under it — the chat thread
 * included — a few hundred times a second. During the gesture the width is now
 * one `style.setProperty` on the pane, which is a paint and nothing else, and
 * React is told once, on release. The stored value and the written one are
 * therefore always the same by the time anything re-renders.
 *
 * The keyboard gets the same control and not a lesser one: focusable, arrow
 * keys by a step, Home/End for the extremes, and Enter to return the pane to
 * the width the stylesheet chose. `role="separator"` with an orientation is
 * what makes that a window splitter to a screen reader rather than a div that
 * mysteriously responds to arrows.
 */
function DockResizer({
  dockRef,
  onResize,
}: {
  dockRef: React.RefObject<HTMLDivElement | null>;
  onResize: (width: number | null) => void;
}) {
  const { t } = useI18n();
  const [dragging, setDragging] = useState(false);
  /** The width the live gesture has reached, and nothing between gestures. */
  const live = useRef<number | null>(null);
  /** How to end the gesture in flight, for anything that is not a pointer. */
  const endRef = useRef<(() => void) | null>(null);

  // Closing the pane mid-drag unmounts the seam, and the listeners are on
  // `window` — they would outlive it, and the next citation would open onto a
  // page that resizes itself as the pointer moves.
  useEffect(() => () => endRef.current?.(), []);

  // The width the pane actually has right now, whether it came from a drag or
  // from the stylesheet. Asked of the element, because the default is a `max()`
  // of viewport terms that this component would otherwise have to re-derive —
  // and a second copy of that expression is a second thing to keep in step.
  const currentWidth = useCallback(
    () => dockRef.current?.getBoundingClientRect().width ?? MIN_DOCK,
    [dockRef],
  );

  // While a drag is in flight the whole window is a drag surface: without
  // this, sweeping across the page selects every paragraph the pointer passes
  // and the cursor flickers back to a caret over text.
  useEffect(() => {
    if (!dragging) return;
    document.body.classList.add('is-resizing-split');
    return () => document.body.classList.remove('is-resizing-split');
  }, [dragging]);

  const startDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    // Or the browser starts a text selection under the grip and the drag
    // fights it the whole way across.
    event.preventDefault();
    if (live.current !== null) return;

    const move = (moved: PointerEvent) => {
      // The pane is on the trailing edge, so its width is everything to the
      // right of the pointer.
      const width = clampDockWidth(window.innerWidth - moved.clientX);
      live.current = width;
      dockRef.current?.style.setProperty('--dock-width', `${width}px`);
    };

    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      window.removeEventListener('blur', end);
      endRef.current = null;
      setDragging(false);
      // One state write for the whole gesture, and the one that makes the
      // width survive the next render.
      if (live.current !== null) onResize(live.current);
      live.current = null;
    };

    live.current = currentWidth();
    endRef.current = end;
    setDragging(true);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    window.addEventListener('blur', end);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    // Left widens the source and right narrows it: the key moves the seam,
    // not the pane, which is what the hand expects of the thing it is holding.
    const step =
      event.key === 'ArrowLeft' ? KEY_STEP : event.key === 'ArrowRight' ? -KEY_STEP : 0;
    if (step !== 0) {
      event.preventDefault();
      onResize(clampDockWidth(currentWidth() + step));
      return;
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      onResize(clampDockWidth(event.key === 'End' ? window.innerWidth : 0));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      onResize(null);
    }
  };

  return (
    <div
      className={dragging ? 'citation-resizer is-dragging' : 'citation-resizer'}
      role="separator"
      aria-orientation="vertical"
      aria-label={t('search.citation.resize')}
      title={t('search.citation.resizeHint')}
      tabIndex={0}
      onPointerDown={startDrag}
      // Back to the stylesheet's width, in the gesture everyone already tries
      // on a splitter.
      onDoubleClick={() => onResize(null)}
      onKeyDown={onKeyDown}
    />
  );
}

/* -------------------------------------------------------------------------- */
/* PDF mode                                                                    */
/* -------------------------------------------------------------------------- */
/**
 * The cited page of the mirrored file, with the passage boxed.
 *
 * `pageWidth`/`pageHeight` come from the index rather than from PDF.js, so the
 * holder reserves the right space before a single byte of the file has
 * arrived. That is what keeps the scroll honest — a container that grows as it
 * paints moves the target out from under the scroll aiming at it — and it is
 * also what lets the modal show the passage as text while the file is still
 * loading, instead of an empty rectangle.
 */
function PdfCitation({
  documentId,
  page,
  bbox,
  pageWidth,
  pageHeight,
  fallback,
}: {
  documentId: string;
  page: number;
  bbox: [number, number, number, number];
  pageWidth: number;
  pageHeight: number;
  fallback: string;
}) {
  const { t } = useI18n();
  const paneRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const markRef = useRef<HTMLDivElement | null>(null);
  const [pdf, setPdf] = useState<PdfDocument | null>(null);
  const [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let opened: PdfDocument | null = null;

    loadPdf(documentFileUrl(documentId))
      .then((loaded) => {
        opened = loaded;
        if (cancelled) loaded.destroy();
        else setPdf(loaded);
      })
      .catch((error: unknown) => {
        // A file that will not open is a state, not a crash: the passage and
        // its page number are still on screen. Logged as well as shown,
        // because the reader needs "unavailable" and whoever runs the
        // deployment needs the reason.
        console.error('The cited document could not be opened:', error);
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
      opened?.destroy();
    };
  }, [documentId]);

  // The render scale follows the pane, so the page fills the modal and the
  // overlay scales with it.
  useEffect(() => {
    const element = paneRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  const scale = width > 0 ? Math.min(width / pageWidth, 2) : 0;

  useEffect(() => {
    if (!pdf || scale <= 0) return;
    let cancelled = false;
    let task: { cancel: () => void } | null = null;

    pdf
      .getPage(page)
      .then((loaded) => {
        const canvas = canvasRef.current;
        if (cancelled || !canvas) return;
        task = renderPage(loaded, canvas, scale);
      })
      .catch(() => setFailed(true));

    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, page, scale]);

  // Centre the box once there is a box to centre. `scrollIntoView` is the
  // obvious call and is wrong here: it scrolls every scrollable ancestor, so
  // opening a citation would move the page behind the modal as well.
  useLayoutEffect(() => {
    const pane = paneRef.current;
    const mark = markRef.current;
    if (!pane || !mark || scale <= 0) return;
    const outer = pane.getBoundingClientRect();
    const inner = mark.getBoundingClientRect();
    const delta = inner.top - outer.top - (outer.height - inner.height) / 2;
    pane.scrollTo({ top: pane.scrollTop + delta, behavior: 'smooth' });
  }, [scale, page]);

  const [x0, top, x1, bottom] = bbox;

  return (
    <div className="citation-pdf" ref={paneRef}>
      {failed && <p className="citation-note">{t('search.citation.fileUnavailable')}</p>}
      {/* Always rendered, above the page. The passage is what was retrieved;
          the page is the proof — and if the file will not open, the passage is
          still the answer. */}
      <blockquote className="citation-quote">{fallback}</blockquote>

      {!failed && (
        <div
          className="citation-page"
          style={{
            width: scale > 0 ? pageWidth * scale : undefined,
            height: scale > 0 ? pageHeight * scale : undefined,
          }}
        >
          <canvas ref={canvasRef} />
          {scale > 0 && (
            <div
              ref={markRef}
              className="citation-highlight"
              style={{
                left: x0 * scale,
                top: top * scale,
                width: Math.max((x1 - x0) * scale, 2),
                height: Math.max((bottom - top) * scale, 2),
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Text mode                                                                   */
/* -------------------------------------------------------------------------- */
/**
 * The source text with the cited character range marked.
 *
 * The string is fetched rather than reused from anything this app already
 * holds, because the offsets index the source's *canonical* form and that
 * normalisation happens on the server. Slicing the raw notice body with these
 * numbers would be a few characters out at the first `&nbsp;` and further out
 * with every one after it — a highlight that drifts further down the page the
 * longer the document, which is exactly the bug that looks like nothing in a
 * short test fixture.
 *
 * If the fetch fails the passage itself is still shown. That is the whole
 * degradation: the answer stays, only its surroundings are missing.
 */
function TextCitation({
  sourceKey,
  charStart,
  charEnd,
  fallback,
}: {
  sourceKey: string;
  charStart: number;
  charEnd: number;
  fallback: string;
}) {
  const { t } = useI18n();
  const paneRef = useRef<HTMLDivElement | null>(null);
  const markRef = useRef<HTMLElement | null>(null);
  const [source, setSource] = useState<SourceText | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchSourceText(sourceKey, controller.signal)
      .then(setSource)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setFailed(true);
      });
    return () => controller.abort();
  }, [sourceKey]);

  const scrollToMark = useCallback(() => {
    const pane = paneRef.current;
    const mark = markRef.current;
    if (!pane || !mark) return;
    const outer = pane.getBoundingClientRect();
    const inner = mark.getBoundingClientRect();
    const delta = inner.top - outer.top - (outer.height - inner.height) / 2;
    pane.scrollTo({ top: pane.scrollTop + delta, behavior: 'smooth' });
  }, []);

  useLayoutEffect(scrollToMark, [source, scrollToMark]);

  if (failed || (source && !source.text)) {
    return (
      <div className="citation-text" ref={paneRef}>
        <p className="citation-note">{t('search.citation.sourceUnavailable')}</p>
        <blockquote className="citation-quote">{fallback}</blockquote>
      </div>
    );
  }

  if (!source) {
    return <p className="citation-note">{t('search.citation.loading')}</p>;
  }

  // Two ways a range arrives, and both end up clamped to the text.
  //
  // Usually the server sends offsets it measured, and they agree with the
  // string because both came from the same call. Some citations carry only the
  // sentence — the similar-awards rows do, because that endpoint returns a
  // passage and not a position — and for those the passage is located here by
  // **exact match**, the same rule the compliance viewer follows: a near-match
  // would box a line that does not say what the citation says, which is worse
  // than no box because it reads as proof.
  let start = Math.max(0, Math.min(charStart, source.text.length));
  let end = Math.max(start, Math.min(charEnd, source.text.length));

  if (end <= start && fallback) {
    const found = source.text.indexOf(fallback.trim());
    if (found >= 0) {
      start = found;
      end = found + fallback.trim().length;
    }
  }

  return (
    <div className="citation-text" ref={paneRef}>
      <div className="notice-body">
        {paragraphsOf(source, start, end, markRef)}
      </div>
    </div>
  );
}

/**
 * The source laid out as its own paragraphs, with the quote marked.
 *
 * The canonical string the offsets index has every paragraph break in it
 * replaced by a full stop — correct for quoting, and a wall of text to read.
 * The server sends back where those breaks were, located in the same string
 * rather than reconstructed, so a block boundary and a highlight are measured
 * against one ruler.
 *
 * A source with no blocks (an extracted PDF, a document with no markup) is
 * rendered as one paragraph rather than split on a guess: an invented break is
 * a line the document does not have, and this pane exists to show what the
 * document does have.
 */
function paragraphsOf(
  source: SourceText,
  start: number,
  end: number,
  markRef: React.RefObject<HTMLElement | null>,
) {
  const blocks =
    source.blocks && source.blocks.length > 0
      ? source.blocks
      : [{ tag: 'p', start: 0, end: source.text.length }];

  let marked = false;
  return blocks.map((block, index) => {
    const body = source.text.slice(block.start, block.end);
    const Tag = (block.tag === 'li' ? 'li' : block.tag || 'p') as 'p' | 'li' | 'h3';

    // The quote as it falls inside this paragraph. A quote that runs across a
    // break is marked in each block it touches, which is what it looks like in
    // the document too.
    const from = Math.max(start, block.start);
    const to = Math.min(end, block.end);
    if (to <= from) {
      return <Tag key={index}>{body}</Tag>;
    }

    const head = source.text.slice(block.start, from);
    const middle = source.text.slice(from, to);
    const tail = source.text.slice(to, block.end);
    // The ref goes on the first marked run: it is what the pane scrolls to,
    // and scrolling to the last one would put the start of the quote above
    // the fold.
    const ref = marked ? undefined : markRef;
    marked = true;

    return (
      <Tag key={index}>
        {head}
        <mark ref={ref as never} className="citation-mark">
          {middle}
        </mark>
        {tail}
      </Tag>
    );
  });
}
