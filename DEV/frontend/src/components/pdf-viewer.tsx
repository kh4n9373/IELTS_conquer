import { useQuery } from '@tanstack/react-query';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useI18n } from '../lib/i18n';
import { testsApi } from '../lib/tests-api';
import { ErrorAlert, Icon, Loading } from './ui';

type Loaded = { doc: PDFDocumentProxy; pages: number };

/** The signed address lives 5 minutes and the file is read once, so it is cached for a shorter time. */
export const pdfUrlKey = (testId: string) => ['pdf-url', testId] as const;

export async function openPdf(url: string): Promise<PDFDocumentProxy> {
  const pdfjs = await import('pdfjs-dist');
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
  // The file is untrusted. No eval-based font paths, and one plain request without range reads, so the
  // signed address needs no extra CORS headers and cannot expire between ranges.
  const wasmUrl = new URL(`${import.meta.env.BASE_URL}pdfjs-wasm/`, window.location.origin).href;
  return pdfjs.getDocument({ url, wasmUrl, isEvalSupported: false, disableRange: true, disableStream: true }).promise;
}

/** Draws one PDF page at a time on a canvas. Never builds HTML from the file. */
export type PdfViewerProps = {
  testId: string;
  hasPdf: boolean;
  label?: ReactNode;
  footer?: ReactNode;
  /** The exam room fetches its own signed link. Without this the teacher's link for `testId` is used. */
  loadUrl?: () => Promise<{ url: string }>;
  queryKey?: readonly unknown[];
  /** Lets the parent own the page, so it survives the viewer being hidden. */
  page?: number;
  onPageChange?: (page: number) => void;
};

const REFRESH_TRIES = 2;

export function PdfViewer({ testId, hasPdf, label, footer, loadUrl, queryKey, page: pageProp, onPageChange }: PdfViewerProps) {
  const { t } = useI18n();
  const url = useQuery({
    queryKey: queryKey ?? pdfUrlKey(testId),
    queryFn: loadUrl ?? (() => testsApi.pdfUrl(testId)),
    enabled: hasPdf,
    staleTime: loadUrl ? Infinity : 60_000,
    // The file is read once and kept in memory, so a new address is only needed when opening fails.
    refetchOnWindowFocus: !loadUrl,
    gcTime: loadUrl ? 0 : undefined,
  });
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState(false);
  const [ownPage, setOwnPage] = useState(1);
  const [attempt, setAttempt] = useState(0);
  const tries = useRef(0);
  const page = pageProp ?? ownPage;
  const setPage = (next: number) => {
    setOwnPage(next);
    onPageChange?.(next);
  };
  const [width, setWidth] = useState(0);
  const frame = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const address = url.data?.url;

  useEffect(() => {
    if (!address) return;
    let cancelled = false;
    let doc: PDFDocumentProxy | null = null;
    setFailed(false);
    openPdf(address).then(
      (opened) => {
        if (cancelled) return void opened.destroy();
        doc = opened;
        tries.current = 0;
        setLoaded({ doc: opened, pages: opened.numPages });
      },
      () => {
        if (cancelled) return;
        // An expired link looks like any failed open: ask for a fresh one a couple of times, quietly.
        if (loadUrl && tries.current < REFRESH_TRIES) {
          tries.current += 1;
          void url.refetch().then(() => setAttempt((n) => n + 1));
        } else setFailed(true);
      },
    );
    return () => {
      cancelled = true;
      void doc?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [address, attempt]);

  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(Math.floor(el.clientWidth)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [loaded]);

  useEffect(() => {
    const target = canvas.current;
    if (!loaded || !target || width === 0) return;
    let task: RenderTask | undefined;
    let cancelled = false;
    loaded.doc.getPage(Math.min(Math.max(page, 1), loaded.pages)).then((p) => {
      if (cancelled) return;
      const base = p.getViewport({ scale: 1 });
      const ratio = window.devicePixelRatio || 1;
      const viewport = p.getViewport({ scale: (width / base.width) * ratio });
      target.width = Math.floor(viewport.width);
      target.height = Math.floor(viewport.height);
      target.style.width = `${Math.floor(viewport.width / ratio)}px`;
      target.style.height = `${Math.floor(viewport.height / ratio)}px`;
      task = p.render({ canvas: target, viewport });
      task.promise.catch(() => undefined);
    }, () => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [loaded, page, width]);

  const shown = loaded ? Math.min(Math.max(page, 1), loaded.pages) : page;
  return (
    <section className="pdf-pane" aria-label={t.tests.pdf.area}>
      <div className="pdf-bar">
        <strong>{loaded ? t.tests.pdf.page(shown, loaded.pages) : t.tests.pdf.area}</strong>
        {label}
      </div>
      {!hasPdf && <p className="muted">{t.tests.pdf.none}</p>}
      {hasPdf && !loaded && !failed && !url.isError && <Loading />}
      {hasPdf && url.isError && <ErrorAlert error={url.error} />}
      {hasPdf && failed && <p className="alert" role="alert"><span>{t.tests.pdf.failed}</span></p>}
      {hasPdf && failed && loadUrl && (
        <button type="button" className="secondary" onClick={() => { tries.current = 0; setFailed(false); void url.refetch().then(() => setAttempt((n) => n + 1)); }}>
          {t.tests.pdf.retry}
        </button>
      )}
      <div className="pdf-frame" ref={frame} hidden={!loaded}>
        <canvas ref={canvas} role="img" aria-label={t.tests.pdf.canvas(shown)} />
      </div>
      {loaded && loaded.pages > 1 && (
        <div className="pdf-nav">
          <button type="button" className="secondary" disabled={shown <= 1} onClick={() => setPage(shown - 1)} aria-label={t.tests.pdf.prev}>
            <Icon name="back" />
          </button>
          <button type="button" className="secondary" disabled={shown >= loaded.pages} onClick={() => setPage(shown + 1)} aria-label={t.tests.pdf.next}>
            <Icon name="arrow" />
          </button>
        </div>
      )}
      {footer}
    </section>
  );
}
