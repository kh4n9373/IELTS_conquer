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

async function openPdf(url: string): Promise<PDFDocumentProxy> {
  const pdfjs = await import('pdfjs-dist');
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
  // The file is untrusted. No eval-based font paths, and one plain request without range reads, so the
  // signed address needs no extra CORS headers and cannot expire between ranges.
  const wasmUrl = new URL(`${import.meta.env.BASE_URL}pdfjs-wasm/`, window.location.origin).href;
  return pdfjs.getDocument({ url, wasmUrl, isEvalSupported: false, disableRange: true, disableStream: true }).promise;
}

/** Draws one PDF page at a time on a canvas. Never builds HTML from the file. */
export function PdfViewer({ testId, hasPdf, label, footer }: { testId: string; hasPdf: boolean; label?: ReactNode; footer?: ReactNode }) {
  const { t } = useI18n();
  const url = useQuery({
    queryKey: pdfUrlKey(testId),
    queryFn: () => testsApi.pdfUrl(testId),
    enabled: hasPdf,
    staleTime: 60_000,
  });
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState(false);
  const [page, setPage] = useState(1);
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
        setLoaded({ doc: opened, pages: opened.numPages });
        setPage((p) => Math.min(p, opened.numPages));
      },
      () => !cancelled && setFailed(true),
    );
    return () => {
      cancelled = true;
      void doc?.destroy();
    };
  }, [address]);

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
    loaded.doc.getPage(page).then((p) => {
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

  return (
    <section className="pdf-pane" aria-label={t.tests.pdf.area}>
      <div className="pdf-bar">
        <strong>{loaded ? t.tests.pdf.page(page, loaded.pages) : t.tests.pdf.area}</strong>
        {label}
      </div>
      {!hasPdf && <p className="muted">{t.tests.pdf.none}</p>}
      {hasPdf && !loaded && !failed && !url.isError && <Loading />}
      {hasPdf && url.isError && <ErrorAlert error={url.error} />}
      {hasPdf && failed && <p className="alert" role="alert"><span>{t.tests.pdf.failed}</span></p>}
      <div className="pdf-frame" ref={frame} hidden={!loaded}>
        <canvas ref={canvas} role="img" aria-label={t.tests.pdf.canvas(page)} />
      </div>
      {loaded && loaded.pages > 1 && (
        <div className="pdf-nav">
          <button type="button" className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label={t.tests.pdf.prev}>
            <Icon name="back" />
          </button>
          <button type="button" className="secondary" disabled={page >= loaded.pages} onClick={() => setPage(page + 1)} aria-label={t.tests.pdf.next}>
            <Icon name="arrow" />
          </button>
        </div>
      )}
      {footer}
    </section>
  );
}
