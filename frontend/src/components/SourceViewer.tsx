import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { documentFileUrl } from '../api/client';
import { loadPdf, renderPage, type PdfDocument } from '../lib/pdf';
import type { NoticeSource, SourceSpan } from '../api/types';
import { useI18n, type TKey } from '../i18n';

/**
 * The tender, open beside the criteria, scrolled to the sentence in question.
 *
 * The card already carries the quote a criterion was read from, which is the
 * claim's warrant. What it cannot do is let a vendor *check* it: a sentence
 * lifted out of a fifty-page Terms of Reference proves nothing about the
 * paragraph it came from, and a bidder deciding whether to spend three weeks on
 * a proposal is entitled to read the surrounding text.
 *
 * Two modes, chosen by the server from what the criteria were actually read out
 * of (`viewer.choose_source`), never by a preference here:
 *
 * * **`pdf`** — the mirrored file, rendered page by page, with the located
 *   lines boxed on top of the page image. This is the mode the feature is for:
 *   the vendor sees the borrower's own document, laid out as the borrower laid
 *   it out.
 * * **`text`** — the notice body, or a document with no page geometry (HTML, a
 *   DOCX). The pane renders the canonical text and the highlight is a character
 *   range. Not a fallback in the apologetic sense: most notices in this corpus
 *   state their criteria in the body and never link a readable document at all,
 *   so this is the common case and it answers the same question.
 *
 * Whichever mode is running, a highlight is only ever the requirement's own
 * verified quote, located by exact match on the server. A criterion whose quote
 * cannot be found in the source simply has nothing to scroll to, and the pane
 * says so rather than guessing at a nearby line.
 */
export default function SourceViewer({
  source,
  activeId,
}: {
  source: NoticeSource;
  /** The requirement being looked at, or null before anything is pressed. */
  activeId: number | null;
}) {
  const { t } = useI18n();

  const spanIds = activeId != null ? (source.highlights[String(activeId)] ?? []) : [];
  const hits = activeId != null ? (source.ranges[String(activeId)] ?? []) : [];

  if (source.spans.length > 0 && source.document) {
    return (
      <PdfSource
        documentId={source.document.id}
        spans={source.spans}
        activeSpanIds={spanIds}
        located={activeId == null || spanIds.length > 0}
      />
    );
  }

  if (source.blocks.length > 0) {
    return (
      <TextSource
        blocks={source.blocks}
        hits={hits}
        located={activeId == null || hits.length > 0}
      />
    );
  }

  return (
    <p className="viewer-empty muted">
      {t(problemKey(source.problem))}
    </p>
  );
}

/**
 * Centre `target` inside `container`, moving nothing else.
 *
 * `scrollIntoView` is the obvious call and it is wrong here: it scrolls *every*
 * scrollable ancestor, so pressing "show me where" moved the pane **and** the
 * window, and the criteria the vendor was reading slid out from under their
 * cursor. Adjusting one element's `scrollTop` is the whole difference, and
 * rectangles are used rather than `offsetTop` because that would depend on
 * which ancestor happens to be positioned.
 */
function scrollWithin(container: HTMLElement, target: HTMLElement): void {
  const outer = container.getBoundingClientRect();
  const inner = target.getBoundingClientRect();
  const delta = inner.top - outer.top - (outer.height - inner.height) / 2;
  container.scrollTo({ top: container.scrollTop + delta, behavior: 'smooth' });
}

/** Why there is nothing to show, in the reader's language. */
function problemKey(problem: string): TKey {
  switch (problem) {
    case 'no_text_layer':
      return 'viewer.problem.noTextLayer';
    case 'file_missing':
      return 'viewer.problem.fileMissing';
    case 'parser_unavailable':
      return 'viewer.problem.parserUnavailable';
    default:
      return 'viewer.problem.none';
  }
}

/* -------------------------------------------------------------------------- */
/* Text mode                                                                   */
/* -------------------------------------------------------------------------- */
/**
 * The source as paragraphs, with the quote marked.
 *
 * Rendered as the tender page renders a notice — same `.notice-body`
 * typography, same block structure, one size smaller so twice as much fits
 * beside the criteria. A vendor comparing the two should recognise the second
 * as the same document: a wall of run-together text reads differently even when
 * every character matches, so the server sends the notice's own blocks rather
 * than one flat string.
 *
 * The offsets arrive already computed, in characters of the same block being
 * rendered. That is the whole reason the server does the locating: finding a
 * quote means normalising apostrophes, entities and whitespace exactly the way
 * the grounding verifier does, and a second implementation of that in
 * TypeScript would drift and start missing highlights nobody could explain.
 */
function TextSource({
  blocks,
  hits,
  located,
}: {
  blocks: NoticeSource['blocks'];
  hits: [number, number, number][];
  located: boolean;
}) {
  const { t } = useI18n();
  const boxRef = useRef<HTMLDivElement | null>(null);
  const markRef = useRef<HTMLElement | null>(null);

  // One entry per block that carries part of the quote. A quote straddling a
  // paragraph break is marked in both, which is what the reader needs to see.
  const marked = useMemo(() => {
    const map = new Map<number, [number, number]>();
    for (const [index, start, end] of hits) map.set(index, [start, end]);
    return map;
  }, [hits]);

  const firstHit = hits.length > 0 ? hits[0][0] : null;

  // After paint, so the element exists and its rectangle is final.
  useLayoutEffect(() => {
    if (boxRef.current && markRef.current) scrollWithin(boxRef.current, markRef.current);
  }, [hits]);

  return (
    <div className="viewer-text notice-body" ref={boxRef}>
      {!located && <p className="viewer-note">{t('viewer.notLocated')}</p>}
      {blocks.map((block, index) => {
        const range = marked.get(index);
        // A list item is rendered as a paragraph with a bullet rather than as a
        // bare `<li>`: the blocks arrive flat, and a list item outside a list
        // is invalid markup that browsers indent inconsistently. The line break
        // is what has to match, and this matches it.
        const Tag = block.tag === 'li' ? 'p' : block.tag;
        const className = block.tag === 'li' ? 'viewer-item' : undefined;
        if (!range) {
          return (
            <Tag key={index} className={className}>
              {block.text}
            </Tag>
          );
        }
        const [start, end] = range;
        return (
          <Tag key={index} className={className}>
            {block.text.slice(0, start)}
            {/* Keyed on the range so React replaces the element rather than
                updating it — a re-used node keeps its animation state, and the
                flash that says "here it is" would only ever play once. */}
            <mark
              key={`${index}-${start}-${end}`}
              ref={index === firstHit ? markRef : undefined}
              className="viewer-mark"
            >
              {block.text.slice(start, end)}
            </mark>
            {block.text.slice(end)}
          </Tag>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* PDF mode                                                                    */
/* -------------------------------------------------------------------------- */
interface PageShape {
  width: number;
  height: number;
}

/**
 * The mirrored file, rendered by PDF.js, with boxes over the located lines.
 *
 * Pages are rendered lazily. A bidding document runs to hundreds of pages and
 * rasterising all of them on open would freeze the tab for the seconds it takes
 * — so each page reserves its space from the geometry the server already sent
 * and paints itself when it comes near the viewport. Reserving the space first
 * is what keeps `scrollTo` honest: a container whose pages grow as they paint
 * would move the target out from under the scroll that was aiming at it.
 */
function PdfSource({
  documentId,
  spans,
  activeSpanIds,
  located,
}: {
  documentId: string;
  spans: SourceSpan[];
  activeSpanIds: string[];
  located: boolean;
}) {
  const { t } = useI18n();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [pdf, setPdf] = useState<PdfDocument | null>(null);
  const [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0);

  // Page geometry comes from the span index rather than from PDF.js, so the
  // placeholders are correct before a single page has been opened.
  const pages = useMemo(() => pageShapes(spans), [spans]);
  const byPage = useMemo(() => groupByPage(spans), [spans]);

  useEffect(() => {
    let cancelled = false;
    loadPdf(documentFileUrl(documentId))
      .then((loaded) => {
        if (cancelled) loaded.destroy();
        else setPdf(loaded);
      })
      .catch((error: unknown) => {
        // A file that will not load is a state, not a crash: the criteria and
        // their quotes are on the left and stay readable.
        //
        // Logged as well as shown, because the two audiences differ and the
        // message they need is not the same one. The vendor is told the
        // document is unavailable, which is all they can act on; whoever is
        // running the deployment needs the reason, and the first version of
        // this swallowed it — a worker that would not start rendered as "the
        // document is not on this server", which was true of nothing.
        console.error('The tender document could not be opened:', error);
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  // The render scale follows the pane, so the document fills whatever width the
  // window left it and the overlay boxes scale with it.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  // Scroll to the first located line whenever the selection changes. The first
  // rather than all of them: a quote spanning four lines has one beginning, and
  // centring on the middle of a block reads as having missed it.
  useLayoutEffect(() => {
    const first = activeSpanIds[0];
    if (!first) return;
    const container = containerRef.current;
    const target = container?.querySelector<HTMLElement>(`[data-span="${first}"]`);
    if (container && target) scrollWithin(container, target);
  }, [activeSpanIds]);

  if (failed) return <p className="viewer-empty muted">{t('viewer.problem.fileMissing')}</p>;

  const scale = width > 0 && pages[0] ? Math.min(width / pages[0].width, 2) : 0;

  return (
    <div className="viewer-pdf" ref={containerRef}>
      {!located && <p className="viewer-note">{t('viewer.notLocated')}</p>}
      {pages.map((shape, index) => (
        <PdfPage
          key={index}
          pdf={pdf}
          pageNumber={index + 1}
          shape={shape}
          scale={scale}
          spans={byPage.get(index + 1) ?? []}
          activeSpanIds={activeSpanIds}
        />
      ))}
    </div>
  );
}

function PdfPage({
  pdf,
  pageNumber,
  shape,
  scale,
  spans,
  activeSpanIds,
}: {
  pdf: PdfDocument | null;
  pageNumber: number;
  shape: PageShape;
  scale: number;
  spans: SourceSpan[];
  activeSpanIds: string[];
}) {
  const holderRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [near, setNear] = useState(false);

  // "Near" rather than "visible": a page that begins painting only once it is
  // on screen is a page the reader watches appear.
  useEffect(() => {
    const element = holderRef.current;
    if (!element) return;
    const observer = new IntersectionObserver(
      ([entry]) => entry.isIntersecting && setNear(true),
      { rootMargin: '150% 0px' },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!pdf || !near || scale <= 0) return;
    let cancelled = false;
    let task: { cancel: () => void } | null = null;

    pdf.getPage(pageNumber).then((page) => {
      const canvas = canvasRef.current;
      if (cancelled || !canvas) return;
      task = renderPage(page, canvas, scale);
    });

    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [pdf, near, pageNumber, scale]);

  const height = scale > 0 ? shape.height * scale : 0;
  const width = scale > 0 ? shape.width * scale : 0;

  return (
    <div
      className="viewer-page"
      ref={holderRef}
      style={{ width: width || undefined, height: height || undefined }}
    >
      <canvas ref={canvasRef} />
      {/* The boxes sit over the canvas, positioned from the same points the
          server measured. Every located line gets an anchor whether or not it
          is the active one, so `scrollTo` has something to aim at even before
          the highlight is painted. */}
      {spans.map((span) => {
        const active = activeSpanIds.includes(span.span_id);
        if (!active) return null;
        return (
          <div
            key={span.span_id}
            data-span={span.span_id}
            className="viewer-highlight"
            style={{
              left: span.bbox.x0 * scale,
              top: span.bbox.top * scale,
              width: (span.bbox.x1 - span.bbox.x0) * scale,
              height: (span.bbox.bottom - span.bbox.top) * scale,
            }}
          />
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Geometry helpers                                                            */
/* -------------------------------------------------------------------------- */
/**
 * One shape per page, from the index.
 *
 * Pages with no indexed line inherit the previous page's shape: a blank page in
 * the middle of a document still occupies space, and skipping it would put
 * every later page's scroll target one page too high.
 */
function pageShapes(spans: SourceSpan[]): PageShape[] {
  const last = spans.reduce((highest, span) => Math.max(highest, span.page), 0);
  const known = new Map<number, PageShape>();
  for (const span of spans) {
    if (!known.has(span.page)) {
      known.set(span.page, { width: span.page_width, height: span.page_height });
    }
  }
  const shapes: PageShape[] = [];
  let previous: PageShape = { width: 612, height: 792 };
  for (let page = 1; page <= last; page += 1) {
    previous = known.get(page) ?? previous;
    shapes.push(previous);
  }
  return shapes;
}

function groupByPage(spans: SourceSpan[]): Map<number, SourceSpan[]> {
  const grouped = new Map<number, SourceSpan[]>();
  for (const span of spans) {
    const list = grouped.get(span.page);
    if (list) list.push(span);
    else grouped.set(span.page, [span]);
  }
  return grouped;
}
