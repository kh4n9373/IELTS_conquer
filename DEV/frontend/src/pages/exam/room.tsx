import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { ExamContentView } from '../../components/exam-content';
import { PdfViewer } from '../../components/pdf-viewer';
import { ErrorAlert, LangSwitch, Loading } from '../../components/ui';
import { createAnswerQueue, type AnswerQueue, type PendingAnswer, type QueueSnapshot } from '../../lib/answer-queue';
import { examApi, type ExamRoom } from '../../lib/exam-api';
import { useI18n } from '../../lib/i18n';
import { errorText } from '../../lib/messages';
import { formatRemaining, serverClock, type ServerClock } from '../../lib/server-clock';
import { AnswerPanel, isAnswered, type Answers } from './answer-panel';
import './exam.css';

type View = 'text' | 'q';
const LAST_MINUTES_MS = 5 * 60_000;
const POLL_MS = 3000;

/** An edit made during a slow save is not part of the request in flight, so a second pass sends it. */
async function sendAll(queue: AnswerQueue) {
  await queue.flush();
  await queue.flush();
}

function useMedia(query: string) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const on = () => setMatches(list.matches);
    list.addEventListener('change', on);
    return () => list.removeEventListener('change', on);
  }, [query]);
  return matches;
}

function useOnline() {
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener('online', up);
    window.addEventListener('offline', down);
    return () => {
      window.removeEventListener('online', up);
      window.removeEventListener('offline', down);
    };
  }, []);
  return online;
}

/** Step 1: the room and the writer claim, fetched once. A refetch must never replace what the student typed. */
export function ExamRoomPage() {
  const { attemptId = '' } = useParams();
  const { t } = useI18n();
  const boot = useQuery({
    queryKey: ['exam-room', attemptId],
    queryFn: async () => {
      // Claim first: a seq this device gets from the room is only safe once older devices are locked out.
      const claimed = await examApi.claim(attemptId).then(
        (c) => ({ token: c.writerToken as string | null, error: null as unknown }),
        (error: unknown) => ({ token: null, error }),
      );
      const room = await examApi.room(attemptId);
      if (room.status === 'in_progress' && claimed.token === null) throw claimed.error;
      return { room, token: claimed.token };
    },
    staleTime: Infinity,
    gcTime: 0,
    retry: 1,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (boot.isPending) return <div className="exam exam-center"><Loading /></div>;
  if (boot.isError)
    return (
      <div className="exam exam-center">
        <ErrorAlert error={boot.error} />
        <p>{t.exam.loadFailed}</p>
        <button type="button" className="xb" onClick={() => void boot.refetch()}>{t.exam.retry}</button>
      </div>
    );
  const { room, token } = boot.data;
  if (room.status !== 'in_progress' || token === null) return <Navigate to={`/results/${attemptId}`} replace />;
  return <QueueGate attemptId={attemptId} room={room} token={token} />;
}

/** Step 2: the answer queue, with whatever an earlier visit left unsent on this device. */
function QueueGate({ attemptId, room, token }: { attemptId: string; room: ExamRoom; token: string }) {
  const tokenRef = useRef(token);
  const clockRef = useRef<ServerClock>(serverClock(room.serverNow));
  const [ready, setReady] = useState<{ queue: AnswerQueue; restored: PendingAnswer[] } | null>(null);

  useEffect(() => {
    const queue = createAnswerQueue(attemptId, async (items) => {
      const result = await examApi.saveAnswers(attemptId, tokenRef.current, items);
      clockRef.current = serverClock(result.serverNow);
      return result;
    });
    room.answers.forEach((a) => queue.seed(a.number, a.seq));
    let live = true;
    void queue.restore().then((restored) => live && setReady({ queue, restored }));
    return () => {
      live = false;
      setReady(null);
      queue.close();
    };
  }, [attemptId, room]);

  if (!ready) return <div className="exam exam-center"><Loading /></div>;
  return <Room attemptId={attemptId} room={room} tokenRef={tokenRef} clockRef={clockRef} queue={ready.queue} restored={ready.restored} />;
}

type RoomProps = {
  attemptId: string;
  room: ExamRoom;
  tokenRef: MutableRefObject<string>;
  clockRef: MutableRefObject<ServerClock>;
  queue: AnswerQueue;
  restored: PendingAnswer[];
};

function initialAnswers(room: ExamRoom, restored: PendingAnswer[]): Answers {
  const out: Answers = {};
  for (const a of room.answers) out[a.number] = { value: a.value, marked: a.marked, seq: a.seq };
  for (const r of restored) if (r.seq > (out[r.number]?.seq ?? 0)) out[r.number] = { value: r.value, marked: r.marked, seq: r.seq };
  return out;
}

const timeFormat = new Intl.DateTimeFormat('vi-VN', { timeStyle: 'medium', hour12: false, timeZone: 'Asia/Ho_Chi_Minh' });

function Room({ attemptId, room, tokenRef, clockRef, queue, restored }: RoomProps) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const phone = useMedia('(max-width: 800px)');
  const online = useOnline();
  // Typing lives here. It starts from the room once and is never overwritten by a refetch.
  const [answers, setAnswers] = useState<Answers>(() => initialAnswers(room, restored));
  const [snap, setSnap] = useState<QueueSnapshot>({ state: 'idle', pending: queue.pending(), savedAt: null });
  const [current, setCurrent] = useState(1);
  const [passage, setPassage] = useState(0);
  const [view, setView] = useState<View>('text');
  const firstPage = (index: number, kind: View) => room.sections[index]?.[kind === 'text' ? 'passagePages' : 'questionPages'][0] ?? 1;
  const [pdfPage, setPdfPage] = useState(() => firstPage(0, 'text'));
  const memo = useRef<Record<string, number>>({});
  const wantFocus = useRef<number | null>(null);
  const [left, setLeft] = useState(() => clockRef.current.remainingMs(room.deadlineAt));
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [claiming, setClaiming] = useState(false);
  const expired = left <= 0;

  useEffect(() => queue.subscribe(setSnap), [queue]);

  // The row may only exist after the passage changed, so focus once the render is done.
  useEffect(() => {
    if (wantFocus.current === null) return;
    document.getElementById(`exam-q${wantFocus.current}`)?.focus();
    wantFocus.current = null;
  });

  useEffect(() => {
    const tick = () => setLeft(clockRef.current.remainingMs(room.deadlineAt));
    const id = setInterval(tick, 250);
    return () => clearInterval(id);
  }, [clockRef, room.deadlineAt]);

  // Time is up: send what is waiting, then wait for the server to close and grade the attempt.
  useEffect(() => {
    if (!expired) return;
    void sendAll(queue);
    const id = setInterval(() => {
      void examApi.room(attemptId).then(
        (fresh) => fresh.status !== 'in_progress' && navigate(`/results/${attemptId}`, { replace: true }),
        () => undefined,
      );
    }, POLL_MS);
    return () => clearInterval(id);
  }, [expired, queue, attemptId, navigate]);

  const stale = snap.state === 'stale';
  const readOnly = stale || snap.state === 'rejected' || expired;
  const offline = snap.state === 'offline' || (!online && snap.pending > 0);
  const answeredCount = room.questions.filter((q) => isAnswered(answers[q.number])).length;
  const markedCount = Object.values(answers).filter((a) => a.marked).length;

  const edit = useCallback(
    (number: number, value: string) => {
      const marked = answers[number]?.marked ?? false;
      const seq = queue.put(number, value, marked);
      setAnswers((prev) => ({ ...prev, [number]: { value, marked, seq } }));
    },
    [answers, queue],
  );
  const mark = (number: number) => {
    const value = answers[number]?.value ?? '';
    const marked = !(answers[number]?.marked ?? false);
    const seq = queue.put(number, value, marked);
    setAnswers((prev) => ({ ...prev, [number]: { value, marked, seq } }));
  };
  const pick = (number: number) => {
    setCurrent(number);
    const owner = room.sections.findIndex((s) => s.numbers.includes(number));
    if (owner >= 0 && owner !== passage) {
      setPassage(owner);
      setPdfPage(memo.current[`${owner}:${phone ? 'text' : view}`] ?? firstPage(owner, phone ? 'text' : view));
    }
    wantFocus.current = number;
  };

  const remember = (page: number) => {
    memo.current[`${passage}:${view}`] = page;
    setPdfPage(page);
  };
  const goTo = (index: number, kind: View, jump: boolean) => {
    setPassage(index);
    setView(kind);
    if (jump) setPdfPage(memo.current[`${index}:${kind}`] ?? firstPage(index, kind));
    const numbers = room.sections[index]?.numbers ?? [];
    if (index !== passage && numbers.length > 0 && !numbers.includes(current)) setCurrent(numbers[0] as number);
  };
  /** A passage tab. On a phone the pane stays as it is and the PDF jumps to that passage's pages. */
  const pickPassage = (index: number) => {
    if (!phone) return goTo(index, view, true);
    setPassage(index);
    setPdfPage(memo.current[`${index}:text`] ?? firstPage(index, 'text'));
    const numbers = room.sections[index]?.numbers ?? [];
    if (numbers.length > 0 && !numbers.includes(current)) setCurrent(numbers[0] as number);
  };

  /** Another device took over and this one wants the pen back. Edits made here while waiting are kept. */
  const claim = async () => {
    setClaiming(true);
    try {
      const { writerToken } = await examApi.claim(attemptId);
      tokenRef.current = writerToken;
      const fresh = await examApi.room(attemptId);
      if (fresh.status !== 'in_progress') return void navigate(`/results/${attemptId}`, { replace: true });
      const waiting = new Map(queue.entries().map((e) => [e.number, e]));
      fresh.answers.forEach((a) => queue.seed(a.number, a.seq));
      const adopt: Answers = {};
      for (const a of fresh.answers) {
        const mine = waiting.get(a.number);
        if (mine) adopt[a.number] = { value: mine.value, marked: mine.marked, seq: queue.put(a.number, mine.value, mine.marked) };
        else if (a.seq > (answers[a.number]?.seq ?? 0)) adopt[a.number] = { value: a.value, marked: a.marked, seq: a.seq };
      }
      for (const [number, mine] of waiting)
        if (!(number in adopt)) adopt[number] = { value: mine.value, marked: mine.marked, seq: queue.put(number, mine.value, mine.marked) };
      setAnswers((prev) => ({ ...prev, ...adopt }));
      queue.resume();
    } catch {
      // The banner and its button stay, so the student can try again.
    } finally {
      setClaiming(false);
    }
  };

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await sendAll(queue);
      if (queue.pending() > 0) {
        setSubmitError(t.exam.unsentBlock(queue.pending()));
        return;
      }
      await examApi.submit(attemptId, tokenRef.current);
      navigate(`/results/${attemptId}`, { replace: true });
    } catch (error) {
      setSubmitError(errorText(error, t.errors));
    } finally {
      setSubmitting(false);
    }
  };

  let status = '';
  if (readOnly) status = t.exam.viewOnly;
  else if (offline) status = t.exam.unsent(snap.pending);
  else if (snap.pending > 0 || snap.state === 'saving') status = t.exam.saving;
  else if (snap.savedAt) status = t.exam.saved(timeFormat.format(new Date(snap.savedAt)));

  const last5 = !expired && left <= LAST_MINUTES_MS;
  const banner = stale
    ? { text: t.exam.otherDevice, tone: 'soft' }
    : snap.state === 'rejected'
      ? { text: t.exam.rejected, tone: 'alert' }
      : expired
        ? { text: t.exam.timeUp, tone: 'alert' }
        : offline
          ? { text: t.exam.offline(snap.pending), tone: 'alert' }
          : last5
            ? { text: t.exam.last5, tone: 'alert' }
            : null;

  const sections = room.sections;
  const shownNumbers = sections[passage]?.numbers;
  const shownQuestions = shownNumbers ? room.questions.filter((q) => shownNumbers.includes(q.number)) : room.questions;
  const hasPdf = room.pageCount !== null;

  return (
    <div className="exam" data-view={view}>
      <header className="exam-head">
        <strong className="exam-title">{room.title}</strong>
        <span role="status" className={`exam-save${offline ? ' is-bad' : ''}`}>{status}</span>
        <span className="exam-tools">
          <LangSwitch />
          <span role="timer" aria-label={t.exam.timeLeft} className={`exam-time${last5 || expired ? ' is-low' : ''}`}>
            {formatRemaining(left)}
          </span>
          <button type="button" className="xb red" disabled={readOnly} onClick={() => setConfirming(true)}>
            {phone ? t.exam.submitShort : t.exam.submit}
          </button>
        </span>
      </header>
      {banner && (
        <div role="alert" className={`exam-banner ${banner.tone}`}>
          <span>{banner.text}</span>
          {stale && (
            <button type="button" className="xb" disabled={claiming} onClick={() => void claim()}>
              {t.exam.claim}
            </button>
          )}
        </div>
      )}
      <div className="exam-main" data-view={view}>
        <div className="exam-switch">
          <span className="exam-tabs">
            {sections.map((s, i) => (
              <button
                key={s.position}
                type="button"
                className={`xb${i === passage ? ' is-on' : ''}`}
                aria-pressed={i === passage}
                onClick={() => pickPassage(i)}
              >
                {s.title}
              </button>
            ))}
          </span>
          <span className="exam-toggle">
            <button type="button" className={`xb${view === 'text' ? ' is-dark' : ''}`} aria-pressed={view === 'text'} onClick={() => goTo(passage, 'text', !phone)}>
              {t.exam.passage}
            </button>
            <button type="button" className={`xb${view === 'q' ? ' is-dark' : ''}`} aria-pressed={view === 'q'} onClick={() => goTo(passage, 'q', !phone)}>
              {t.exam.questions}
            </button>
          </span>
        </div>
        <section className="exam-paper" aria-label={t.exam.pdfArea}>
          {room.authoringMode === 'composed' ? (
            <ExamContentView section={room.content?.sections[passage]} />
          ) : (
          <PdfViewer
            testId={attemptId}
            hasPdf={hasPdf}
            queryKey={['exam-pdf', attemptId]}
            loadUrl={() => examApi.pdfUrl(attemptId)}
            page={pdfPage}
            onPageChange={remember}
          />
          )}
        </section>
        <AnswerPanel
          questions={shownQuestions}
          answers={answers}
          current={current}
          phone={phone}
          locked={readOnly}
          onPick={pick}
          onEdit={edit}
          onMark={mark}
        />
        <div className="exam-step">
          <button type="button" className="xb" onClick={() => pick(Math.max(1, current - 1))}>{t.exam.prev}</button>
          <button type="button" className="xb is-dark" onClick={() => pick(Math.min(room.questions.length, current + 1))}>{t.exam.next}</button>
        </div>
      </div>
      {confirming && (
        <div className="exam-veil">
          <div role="alertdialog" aria-modal="true" aria-label={t.exam.confirmLabel} className="exam-dialog">
            <h2>{t.exam.confirmTitle}</h2>
            {!phone && (
              <div className="exam-counts">
                <span><strong>{answeredCount}</strong>{t.exam.answered}</span>
                <span><strong>{room.questions.length - answeredCount}</strong>{t.exam.blankLeft}</span>
                <span><strong>{markedCount}</strong>{t.exam.forReview}</span>
              </div>
            )}
            <p>{phone ? t.exam.confirmPhone(answeredCount, room.questions.length) : t.exam.confirmWarn}</p>
            {submitError && <p className="alert" role="alert"><span>{submitError}</span></p>}
            <div className="exam-actions">
              <button type="button" className="xb" autoFocus disabled={submitting} onClick={() => setConfirming(false)}>{t.exam.keepWorking}</button>
              <button type="button" className="xb red" disabled={submitting || readOnly} onClick={() => void submit()}>
                {submitting ? t.exam.sending : t.exam.submit}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
