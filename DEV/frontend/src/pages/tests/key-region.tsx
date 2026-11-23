import { useQuery } from '@tanstack/react-query';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import { useEffect, useRef, useState } from 'react';
import { openPdf } from '../../components/pdf-viewer';
import { ApiError } from '../../lib/api';
import { useI18n } from '../../lib/i18n';
import { testsApi } from '../../lib/tests-api';

type Box = [number, number, number, number];

const MARGIN_X = 0.02;
const MARGIN_Y = 0.012;
const MAX_HEIGHT = 240;
const MAX_ZOOM = 5;
/** Card padding and the crop border, which the drawing must fit inside. */
const CARD_INSET = 24;

/** Rows stored before boxes became page fractions hold pixels, so they count as "no box". */
function usableBox(bbox: number[] | null): Box | null {
  if (bbox?.length !== 4 || !bbox.every(Number.isFinite)) return null;
  const [x0, y0, x1, y1] = bbox as Box;
  return x0 >= 0 && y0 >= 0 && x0 < x1 && y0 < y1 && x1 <= 1 && y1 <= 1 ? [x0, y0, x1, y1] : null;
}

function withMargin([x0, y0, x1, y1]: Box): Box {
  return [Math.max(0, x0 - MARGIN_X), Math.max(0, y0 - MARGIN_Y), Math.min(1, x1 + MARGIN_X), Math.min(1, y1 + MARGIN_Y)];
}

type Props = {
  testId: string;
  /** The stored file these rows belong to. */
  revision: string | null;
  number: number | null;
  page: number | null;
  bbox: number[] | null;
  onFileChanged: () => void;
};

/** Shows where a stored answer came from. Remounted for each stored file; its failures stay in this card. */
export function KeyRegion({ testId, revision, number, page, bbox, onFileChanged }: Props) {
  const { t } = useI18n();
  const wanted = number !== null && page !== null && revision !== null;
  const url = useQuery({
    queryKey: ['key-source-url', testId, revision],
    queryFn: () => testsApi.keySourceUrl(testId, revision as string),
    enabled: wanted,
    staleTime: 60_000,
    gcTime: 0,
  });
  const address = url.data?.url;
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [drawn, setDrawn] = useState<string | null>(null);
  const [drawFailed, setDrawFailed] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const card = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);

  // The file moved on while these rows were on screen: fetch the rows that match it.
  const fileChanged = url.error instanceof ApiError && url.error.code === 'key_source_changed';
  useEffect(() => {
    if (fileChanged) onFileChanged();
    // Once per refusal. The new revision remounts this card.
  }, [fileChanged]);

  const box = usableBox(bbox);
  const target = wanted ? `${number}:${page}:${box?.join(',') ?? 'page'}` : null;

  useEffect(() => {
    if (!address) return;
    let cancelled = false;
    let opened: PDFDocumentProxy | null = null;
    setLoadFailed(false);
    openPdf(address).then(
      (next) => {
        if (cancelled) return void next.destroy();
        opened = next;
        setDoc(next);
      },
      () => !cancelled && setLoadFailed(true),
    );
    return () => {
      cancelled = true;
      setDoc(null);
      void opened?.destroy();
    };
  }, [address]);

  useEffect(() => {
    const el = card.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(Math.max(0, Math.floor(el.clientWidth) - CARD_INSET)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    // Cleared first: whatever happens next, the previous row's crop is gone.
    host.current?.replaceChildren();
    setDrawn(null);
    setDrawFailed(null);
    if (!doc || !target || page === null || number === null || width === 0) return;
    let task: RenderTask | undefined;
    let cancelled = false;
    const fail = () => !cancelled && setDrawFailed(target);
    doc.getPage(page).then((p) => {
      if (cancelled) return;
      const base = p.getViewport({ scale: 1 });
      const [x0, y0, x1, y1] = box ? withMargin(box) : ([0, 0, 1, 1] as Box);
      const cropWidth = (x1 - x0) * base.width;
      const cropHeight = (y1 - y0) * base.height;
      const fit = Math.min(width / cropWidth, MAX_HEIGHT / cropHeight, MAX_ZOOM);
      const scale = fit * (window.devicePixelRatio || 1);
      const viewport = p.getViewport({ scale, offsetX: -x0 * base.width * scale, offsetY: -y0 * base.height * scale });
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.ceil(cropWidth * scale));
      canvas.height = Math.max(1, Math.ceil(cropHeight * scale));
      canvas.style.width = `${Math.ceil(cropWidth * fit)}px`;
      canvas.style.height = `${Math.ceil(cropHeight * fit)}px`;
      canvas.setAttribute('role', 'img');
      canvas.setAttribute('aria-label', t.tests.key.regionImage(number));
      task = p.render({ canvas, viewport });
      task.promise.then(() => {
        if (cancelled) return;
        host.current?.replaceChildren(canvas);
        setDrawn(target);
      }, fail);
    }, fail);
    return () => {
      cancelled = true;
      task?.cancel();
    };
    // `target` already stands for the row, page and box.
  }, [doc, target, width]);

  const failed = wanted && (url.isError || loadFailed || drawFailed === target);
  const ready = wanted && !failed && drawn === target;
  let note: string | null = null;
  if (number === null) note = t.tests.key.regionPick;
  else if (page === null) note = t.tests.key.regionNone;
  else if (failed) note = t.tests.key.regionFailed;
  else if (!ready) note = t.tests.key.regionLoading;
  else if (!box) note = t.tests.key.regionWhole;

  return (
    <div className="key-region" ref={card}>
      <strong>{number === null ? t.tests.key.region : t.tests.key.regionFor(number)}</strong>
      <div className="key-crop" ref={host} hidden={!ready} />
      {note && <p role="status">{note}</p>}
    </div>
  );
}
