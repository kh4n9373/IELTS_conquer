import type { AnswerItem, SaveResult } from './exam-api';

export type SendBatch = (items: AnswerItem[]) => Promise<SaveResult>;
export type PendingAnswer = { number: number; value: string; marked: boolean; seq: number };
/** `stale` means another device took over. `rejected` means the server refuses this attempt's saves. */
export type QueueState = 'idle' | 'saving' | 'offline' | 'stale' | 'rejected';
export type QueueSnapshot = { state: QueueState; pending: number; savedAt: string | null };

export type AnswerQueue = {
  /** Keeps the answer on this device at once and sends it soon. Returns the answer's sequence number. */
  put: (number: number, value: string, marked: boolean) => number;
  /** Tells the queue the highest sequence the server already holds for a question. */
  seed: (number: number, seq: number) => void;
  /** Loads answers that never reached the server, e.g. before a reload. Run once after creation. */
  restore: () => Promise<PendingAnswer[]>;
  flush: () => Promise<void>;
  pending: () => number;
  entries: () => PendingAnswer[];
  /** After a new claim: sends whatever is waiting. */
  resume: () => void;
  subscribe: (listener: (snapshot: QueueSnapshot) => void) => () => void;
  close: () => void;
};

const DEBOUNCE_MS = 800;
const MAX_WAIT_MS = 10_000;
const RETRY_MS = 10_000;
const BATCH = 40;
const DB_NAME = 'panxg-exam';
const STORE = 'answers';

type Row = PendingAnswer & { id: string; attemptId: string };

/** Only answer text lives here. Never the PDF link or anything else the server signs. */
function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') return reject(new Error('no_indexeddb'));
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const store = req.result.createObjectStore(STORE, { keyPath: 'id' });
      store.createIndex('attempt', 'attemptId');
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

const done = (tx: IDBTransaction) =>
  new Promise<void>((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = tx.onabort = () => reject(tx.error);
  });

export function createAnswerQueue(attemptId: string, send: SendBatch): AnswerQueue {
  const entries = new Map<number, PendingAnswer>();
  const seqs = new Map<number, number>();
  const listeners = new Set<(s: QueueSnapshot) => void>();
  let state: QueueState = 'idle';
  let savedAt: string | null = null;
  let debounce: ReturnType<typeof setTimeout> | undefined;
  let maxWait: ReturnType<typeof setTimeout> | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<void> | null = null;
  let closed = false;
  let db: Promise<IDBDatabase> | null = null;

  const key = (n: number) => `${attemptId}:${n}`;
  const snapshot = (): QueueSnapshot => ({ state, pending: entries.size, savedAt });
  const emit = () => listeners.forEach((l) => l(snapshot()));
  const setState = (next: QueueState) => {
    state = next;
    emit();
  };
  const database = () => (db ??= openDb());
  // Storage can be blocked or full. The queue then still works in memory for this visit.
  const store = (run: (s: IDBObjectStore) => void, mode: IDBTransactionMode = 'readwrite') =>
    database()
      .then((d) => {
        const tx = d.transaction(STORE, mode);
        run(tx.objectStore(STORE));
        return done(tx);
      })
      .catch(() => undefined);

  const clearTimers = () => {
    clearTimeout(debounce);
    clearTimeout(maxWait);
    debounce = maxWait = undefined;
  };

  const schedule = () => {
    if (closed || state === 'stale' || state === 'rejected') return;
    clearTimeout(debounce);
    debounce = setTimeout(() => void flush(), DEBOUNCE_MS);
    maxWait ??= setTimeout(() => void flush(), MAX_WAIT_MS);
  };

  const scheduleRetry = () => {
    clearTimeout(retry);
    if (!closed) retry = setTimeout(() => void flush(), RETRY_MS);
  };

  async function drain(): Promise<void> {
    while (entries.size > 0) {
      const batch = [...entries.values()].slice(0, BATCH);
      setState('saving');
      let result: SaveResult;
      try {
        result = await send(batch.map((e) => ({ number: e.number, value: e.value, markedForReview: e.marked, seq: e.seq })));
      } catch (error) {
        const status = (error as { status?: number }).status;
        if (status === 409) return setState('stale');
        if (status !== undefined && status >= 400 && status < 500 && status !== 408 && status !== 429) return setState('rejected');
        setState('offline');
        return scheduleRetry();
      }
      savedAt = result.serverNow;
      // Accepted and late both mean the server holds the text (late ones in a separate table), so neither is resent.
      const sent = new Map(batch.map((e) => [e.number, e.seq]));
      const gone = [...result.accepted.map((a) => a.number), ...result.late];
      const removed: number[] = [];
      for (const number of gone) {
        const current = entries.get(number);
        if (current && current.seq === sent.get(number)) {
          entries.delete(number);
          removed.push(number);
        }
      }
      if (removed.length) void store((s) => removed.forEach((n) => s.delete(key(n))));
      if (entries.size === 0 || batch.every((b) => !removed.includes(b.number))) break;
    }
    setState('idle');
  }

  function flush(): Promise<void> {
    clearTimers();
    clearTimeout(retry);
    if (state === 'stale' || state === 'rejected') return Promise.resolve();
    inFlight ??= drain().finally(() => {
      inFlight = null;
      // An edit made during this save was not in it. Nothing else would send it.
      if (entries.size > 0 && state === 'idle') schedule();
    });
    return inFlight;
  }

  const online = () => void flush();
  window.addEventListener('online', online);
  // An empty save is how a device that lost the claim finds out, even while its student is only reading.
  const heartbeat = setInterval(() => {
    if (entries.size === 0 && state === 'idle' && !inFlight) {
      inFlight = send([])
        .then(() => undefined)
        .catch((error: unknown) => {
          if ((error as { status?: number }).status === 409) setState('stale');
        })
        .finally(() => {
          inFlight = null;
          if (entries.size > 0 && state === 'idle') schedule();
        });
    }
  }, MAX_WAIT_MS);

  return {
    put(number, value, marked) {
      const seq = (seqs.get(number) ?? 0) + 1;
      seqs.set(number, seq);
      const row: Row = { id: key(number), attemptId, number, value, marked, seq };
      entries.set(number, { number, value, marked, seq });
      void store((s) => s.put(row));
      emit();
      schedule();
      return seq;
    },
    seed(number, seq) {
      seqs.set(number, Math.max(seqs.get(number) ?? 0, seq));
    },
    async restore() {
      const rows = await database()
        .then(
          (d) =>
            new Promise<Row[]>((resolve, reject) => {
              const req = d.transaction(STORE, 'readonly').objectStore(STORE).index('attempt').getAll(attemptId);
              req.onsuccess = () => resolve(req.result as Row[]);
              req.onerror = () => reject(req.error);
            }),
        )
        .catch(() => [] as Row[]);
      for (const r of rows) {
        const mine = entries.get(r.number);
        if (mine && mine.seq >= r.seq) continue; // typed again since the restore began
        entries.set(r.number, { number: r.number, value: r.value, marked: r.marked, seq: r.seq });
        seqs.set(r.number, Math.max(seqs.get(r.number) ?? 0, r.seq));
      }
      emit();
      if (entries.size) schedule();
      return [...entries.values()];
    },
    flush,
    pending: () => entries.size,
    entries: () => [...entries.values()],
    resume() {
      if (state === 'stale' || state === 'rejected') state = 'idle';
      emit();
      void flush();
    },
    subscribe(listener) {
      listeners.add(listener);
      listener(snapshot());
      return () => void listeners.delete(listener);
    },
    close() {
      closed = true;
      clearTimers();
      clearTimeout(retry);
      clearInterval(heartbeat);
      window.removeEventListener('online', online);
      listeners.clear();
    },
  };
}
