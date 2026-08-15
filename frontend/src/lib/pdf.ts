/**
 * PDF.js, loaded once per session, for whichever pane needs it.
 *
 * Two components open mirrored documents — the criteria pane beside a tender
 * (`SourceViewer`) and the citation modal over a search result
 * (`CitationViewer`) — and both need the same non-obvious set-up. This module
 * exists so there is one copy of it: the worker wiring below was a bug fix, and
 * a second copy would be the copy that does not get the next fix.
 */

/**
 * The parts of PDF.js these panes use.
 *
 * Hand-written rather than imported from `pdfjs-dist`: the library's own types
 * pull its whole module graph into every file that mentions a page, and the
 * surface used here is four calls wide.
 */
export interface PdfDocument {
  getPage: (n: number) => Promise<{
    getViewport: (options: { scale: number }) => { width: number; height: number };
    render: (options: {
      canvas: HTMLCanvasElement;
      canvasContext: CanvasRenderingContext2D;
      viewport: { width: number; height: number };
      transform?: number[];
    }) => { cancel: () => void };
  }>;
  destroy: () => void;
}

let pdfjsPromise: Promise<typeof import('pdfjs-dist')> | null = null;

/**
 * Load PDF.js on first use, once per session.
 *
 * A dynamic import because the library and its worker are by far the largest
 * thing this bundle could ship, and the places that need it are few. Every
 * other route should not pay for it.
 *
 * **The worker is handed over as a port, not as a URL**, and that is a fix
 * rather than a preference. Setting `workerSrc` to the library's own
 * `pdf.worker.min.mjs` asks the browser to import a `.mjs` file, and nginx's
 * bundled mime.types has no entry for that extension — so it arrived as
 * `application/octet-stream`, `nosniff` refused to execute it, and PDF.js fell
 * back to its "fake worker", which then failed too. The page reported "the
 * document is not available", which was true of nothing. Vite's `?worker`
 * import emits an ordinary `.js` chunk and constructs it here, so the
 * extension never comes up. The server's mime.types is fixed as well, because
 * the next `.mjs` asset should not have to rediscover this.
 */
export async function loadPdf(url: string): Promise<PdfDocument> {
  if (!pdfjsPromise) {
    pdfjsPromise = import('pdfjs-dist').then(async (pdfjs) => {
      const { default: PdfWorker } = await import(
        'pdfjs-dist/build/pdf.worker.min.mjs?worker'
      );
      pdfjs.GlobalWorkerOptions.workerPort = new PdfWorker();
      return pdfjs;
    });
  }
  const pdfjs = await pdfjsPromise;
  // The session cookie travels: a document a vendor handed over is served only
  // to signed-in callers, and without this the viewer would 404 on exactly the
  // documents the vendor supplied themselves.
  return (await pdfjs.getDocument({ url, withCredentials: true })
    .promise) as unknown as PdfDocument;
}

/**
 * Render one page onto one canvas at `scale`. Returns the render task.
 *
 * Shared for the same reason as the loader: the device-pixel-ratio handling
 * below is the kind of detail that is right in one copy and subtly wrong in
 * the next.
 */
export function renderPage(
  page: Awaited<ReturnType<PdfDocument['getPage']>>,
  canvas: HTMLCanvasElement,
  scale: number,
): { cancel: () => void } | null {
  const viewport = page.getViewport({ scale });
  // Device pixels for the bitmap, CSS pixels for the box: without this the
  // text is soft on every screen a laptop has had for a decade.
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(viewport.width * ratio);
  canvas.height = Math.floor(viewport.height * ratio);
  canvas.style.width = `${viewport.width}px`;
  canvas.style.height = `${viewport.height}px`;

  const context = canvas.getContext('2d');
  if (!context) return null;

  // The ratio is handed to PDF.js as a transform rather than set on the
  // context first. Setting it there looks equivalent and is not: the renderer
  // installs the viewport's own transform over whatever the context had, so
  // the scaling was discarded and the page was drawn at 1× into a 2× bitmap —
  // which on a retina screen came out blank.
  const task = page.render({
    canvas,
    canvasContext: context,
    viewport,
    transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
  });
  (task as unknown as { promise: Promise<void> }).promise.catch(() => {
    /* a cancelled render is the normal way a scroll interrupts one */
  });
  return task;
}
