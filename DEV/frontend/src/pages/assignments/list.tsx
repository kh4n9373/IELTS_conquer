import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { useMe } from '../../app/session';
import { Disclosure, Empty, ErrorAlert, Icon, Loading, order, PageHead } from '../../components/ui';
import { api, type ClassDetail, type ClassSummary } from '../../lib/api';
import { deviceKind, examApi, type Assignment, type AssignmentMode, type MyAssignment, type ReleasePolicy } from '../../lib/exam-api';
import { useI18n, type Lang } from '../../lib/i18n';
import { useFreshItems } from '../../lib/motion';
import { testsApi } from '../../lib/tests-api';
import './assignments.css';

const ZONE = 'Asia/Ho_Chi_Minh';
const LOCALE: Record<Lang, string> = { vi: 'vi-VN', en: 'en-GB' };

/** Date and time in Vietnam time, for example "19:00, 28 Nov 2026". */
export function useDateTime() {
  const { lang } = useI18n();
  return useMemo(() => {
    const f = new Intl.DateTimeFormat(LOCALE[lang], { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: ZONE });
    return (iso: string) => f.format(new Date(iso));
  }, [lang]);
}

/** A datetime-local value is read as Vietnam time. */
const toIso = (local: string) => new Date(`${local}:00+07:00`).toISOString();

export function AssignmentsPage() {
  const me = useMe();
  if (!me.data) return null;
  return me.data.role === 'teacher' ? <TeacherAssignments /> : <MyAssignments />;
}

function defaultRelease(attempts: number): ReleasePolicy {
  return attempts === 1 ? 'after_submit' : 'after_last';
}

function NewAssignment({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const navigate = useNavigate();
  const tests = useQuery({ queryKey: ['tests'], queryFn: testsApi.list });
  const classes = useQuery({ queryKey: ['classes'], queryFn: () => api.get<ClassSummary[]>('/api/classes') });
  const [testId, setTestId] = useState('');
  const [classId, setClassId] = useState('');
  const [all, setAll] = useState(true);
  const [chosen, setChosen] = useState<string[]>([]);
  const [opens, setOpens] = useState('');
  const [due, setDue] = useState('');
  const [attempts, setAttempts] = useState(1);
  const [mode, setMode] = useState<AssignmentMode>('exam');
  const [release, setRelease] = useState<ReleasePolicy | null>(null);
  const [releaseAt, setReleaseAt] = useState('');
  const klass = useQuery({ queryKey: ['class', classId], queryFn: () => api.get<ClassDetail>(`/api/classes/${classId}`), enabled: classId !== '' });

  const published = (tests.data ?? []).filter((x) => x.status === 'published');
  const activeClasses = (classes.data ?? []).filter((c) => c.status === 'active');
  const members = klass.data?.members ?? [];
  const policy = release ?? defaultRelease(attempts);
  const recipients = all ? members.length : chosen.length;
  const ready = testId !== '' && classId !== '' && opens !== '' && due !== '' && (all ? true : chosen.length > 0) && (policy !== 'scheduled' || releaseAt !== '');

  const create = useMutation({
    mutationFn: (e: FormEvent) => {
      e.preventDefault();
      return examApi.createAssignment({
        testId,
        classId,
        ...(all ? {} : { studentIds: chosen }),
        opensAt: toIso(opens),
        dueAt: toIso(due),
        maxAttempts: attempts,
        mode,
        releasePolicy: policy,
        ...(policy === 'scheduled' ? { releaseAt: toIso(releaseAt) } : {}),
      });
    },
    onSuccess: (row) => {
      client.invalidateQueries({ queryKey: ['assignments'] });
      onDone();
      navigate(`/assignments/${row.id}`, { viewTransition: true });
    },
  });

  const who = all ? t.assignments.wholeClass(klass.data ? members.length : null) : t.assignments.studentsCount(chosen.length);
  const releaseLabels = t.assignments.releaseOpts;
  const policies: ReleasePolicy[] = ['after_submit', 'after_last', 'scheduled'];

  return (
    <div className="asg-layout">
      <form className="asg-form" onSubmit={(e) => create.mutate(e)}>
        <label className="plain wide">{t.assignments.test}
          <select value={testId} onChange={(e) => setTestId(e.target.value)} required>
            <option value="">{published.length === 0 && !tests.isPending ? t.assignments.noPublished : t.assignments.pickTest}</option>
            {published.map((x) => <option key={x.id} value={x.id}>{t.assignments.testOption(x.title, null, x.durationMinutes)}</option>)}
          </select>
        </label>
        <label className="plain">{t.assignments.class}
          <select value={classId} onChange={(e) => { setClassId(e.target.value); setChosen([]); }} required>
            <option value="">{t.assignments.pickClass}</option>
            {activeClasses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <fieldset>
          <legend>{t.assignments.recipients}</legend>
          <span className="seg">
            <button type="button" className="secondary" aria-pressed={all} onClick={() => setAll(true)}>{t.assignments.wholeClass(klass.data ? members.length : null)}</button>
            <button type="button" className="secondary" aria-pressed={!all} onClick={() => setAll(false)}>{t.assignments.choose}</button>
          </span>
        </fieldset>
        {!all && classId !== '' && (
          <div className="picks wide" role="group" aria-label={t.assignments.recipients}>
            {members.map((m) => (
              <label key={m.id}>
                <input type="checkbox" checked={chosen.includes(m.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, m.id] : chosen.filter((x) => x !== m.id))} />
                <span>{m.displayName}</span>
              </label>
            ))}
          </div>
        )}
        <label className="plain">{t.assignments.opens}
          <input type="datetime-local" value={opens} onChange={(e) => setOpens(e.target.value)} required />
        </label>
        <label className="plain">{t.assignments.due}
          <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} required />
        </label>
        <fieldset>
          <legend>{t.assignments.attempts}</legend>
          <span className="seg">
            {[1, 2, 3, 4, 5].map((n) => (
              <button key={n} type="button" className="secondary" aria-pressed={n === attempts} onClick={() => { setAttempts(n); setRelease(null); }}>{n}</button>
            ))}
          </span>
        </fieldset>
        <fieldset>
          <legend>{t.assignments.mode}</legend>
          <span className="seg">
            {(['exam', 'practice'] as const).map((m) => (
              <button key={m} type="button" className="secondary" aria-pressed={m === mode} onClick={() => setMode(m)}>{t.assignments.modes[m]}</button>
            ))}
          </span>
        </fieldset>
        <fieldset className="wide">
          <legend>{t.assignments.release}</legend>
          <span className="seg stack-v">
            {policies.map((p) => (
              <button key={p} type="button" className="secondary" aria-pressed={p === policy} onClick={() => setRelease(p)}>{releaseLabels[p]}</button>
            ))}
          </span>
        </fieldset>
        {policy === 'scheduled' && (
          <label className="plain wide">{t.assignments.releaseAt}
            <input type="datetime-local" value={releaseAt} onChange={(e) => setReleaseAt(e.target.value)} required />
          </label>
        )}
        <div className="wide"><ErrorAlert error={create.error ?? tests.error ?? classes.error ?? klass.error} /></div>
        <div className="row-actions end wide">
          <button type="button" className="secondary" onClick={onDone}>{t.common.cancel}</button>
          <button type="submit" disabled={!ready || create.isPending}>{t.assignments.submit}</button>
        </div>
      </form>
      <aside aria-label={t.assignments.summaryTitle}>
        <h2>{t.assignments.summaryTitle}</h2>
        <p>{t.assignments.summary(recipients > 0 || all ? who : t.assignments.selected, attempts, t.assignments.modes[mode], releaseLabels[policy])}</p>
        <p className="muted">{t.assignments.rule}</p>
      </aside>
    </div>
  );
}

function TeacherAssignments() {
  const { t } = useI18n();
  const dateTime = useDateTime();
  const [creating, setCreating] = useState(false);
  const list = useQuery({ queryKey: ['assignments'], queryFn: examApi.listAssignments });
  const listRef = useFreshItems<HTMLOListElement>(list.data?.map((x) => x.id).join() ?? '');
  return (
    <>
      <PageHead eyebrow={t.assignments.eyebrow} title={t.assignments.teacherTitle} lede={t.assignments.teacherLede}>
        {!creating && (
          <button type="button" className="auto" onClick={() => setCreating(true)}>
            <span>{t.assignments.newButton}</span>
            <Icon name="plus" />
          </button>
        )}
      </PageHead>
      {creating && (
        <section className="in" style={order(3)} aria-labelledby="new-asg">
          <div className="section-head"><h2 id="new-asg">{t.assignments.newTitle}</h2></div>
          <NewAssignment onDone={() => setCreating(false)} />
        </section>
      )}
      <section className="toc-wrap in" style={order(4)}>
        <div className="section-head"><h2>{t.assignments.listTitle}</h2></div>
        <ErrorAlert error={list.error} />
        {list.isPending && <Loading />}
        {list.data?.length === 0 && <Empty title={t.assignments.noneTitle}>{t.assignments.noneBody}</Empty>}
        <ol className="toc" ref={listRef}>
          {list.data?.map((row: Assignment, i) => (
            <li key={row.id} data-key={row.id}>
              <Link className="toc-row" to={`/assignments/${row.id}`} viewTransition>
                <span className="toc-no" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                <span className="toc-name">
                  <span>{row.testTitle}</span>
                  <span className="muted test-meta">{t.assignments.meta(row.className, dateTime(row.dueAt), row.recipientCount)}</span>
                </span>
                <span className="toc-leader" aria-hidden="true" />
                <span className={`tag${row.status === 'open' ? ' tag-live' : ''}`}>{t.assignments.status[row.status]}</span>
                <span className="toc-arrow" aria-hidden="true"><Icon name="arrow" /></span>
              </Link>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}

type Action = { label: string; run: (() => void) | null; primary: boolean };

function MyAssignments() {
  const { t } = useI18n();
  const dateTime = useDateTime();
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ['my-assignments'], queryFn: examApi.myAssignments });
  const listRef = useFreshItems<HTMLUListElement>(list.data?.map((x) => x.id).join() ?? '');
  const start = useMutation({
    mutationFn: (id: string) => examApi.startAttempt(id, deviceKind()),
    onSuccess: ({ attemptId }) => navigate(`/exam/${attemptId}`, { viewTransition: true }),
  });

  const actionOf = (row: MyAssignment): Action => {
    const used = row.attemptsUsed >= row.maxAttempts;
    if (row.status === 'open' && (row.inProgress || !used)) {
      return { label: row.inProgress ? t.my.cont : t.my.start, run: () => start.mutate(row.id), primary: !row.inProgress };
    }
    if (row.attemptsUsed > 0 && row.latestAttemptId) {
      const id = row.latestAttemptId;
      return { label: t.my.view, run: () => navigate(`/results/${id}`, { viewTransition: true }), primary: false };
    }
    return { label: row.status === 'upcoming' ? t.my.notOpen : row.status === 'open' ? t.my.start : t.my.closed, run: null, primary: false };
  };
  const kicker = (row: MyAssignment) =>
    row.inProgress ? t.my.status.in_progress : row.attemptsUsed > 0 && row.status !== 'open' ? t.my.status.done : t.my.status[row.status];

  return (
    <>
      <PageHead title={t.my.title} lede={t.my.lede} />
      <section className="in" style={order(3)}>
        <ErrorAlert error={list.error ?? start.error} />
        {list.isPending && <Loading />}
        {list.data?.length === 0 && <Empty title={t.my.emptyTitle}>{t.my.emptyBody}</Empty>}
        <ul className="my-list" ref={listRef}>
          {list.data?.map((row) => {
            const action = actionOf(row);
            const go = row.status === 'open' && action.run !== null && action.primary;
            return (
              <li key={row.id} data-key={row.id} className={`my-item${go ? ' is-go' : action.run === null ? ' is-off' : ''}`}>
                <div>
                  <span className="kicker">{kicker(row)}</span>
                  <h2>{row.testTitle ?? t.my.untitled}</h2>
                  <span className="muted">
                    {[row.className, row.status === 'upcoming' ? t.my.metaOpens(dateTime(row.opensAt)) : t.my.meta(dateTime(row.dueAt), null, row.maxAttempts), t.assignments.modes[row.mode], t.my.used(row.attemptsUsed, row.maxAttempts)].filter(Boolean).join(' · ')}
                  </span>
                </div>
                <button type="button" className={`cta secondary${action.primary ? ' is-primary' : ''}`} disabled={action.run === null || start.isPending} onClick={() => action.run?.()}>{action.label}</button>
              </li>
            );
          })}
        </ul>
      </section>
    </>
  );
}
