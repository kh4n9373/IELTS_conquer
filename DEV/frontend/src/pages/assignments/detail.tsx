import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { ErrorAlert, Loading, Notice, order, PageHead, useConfirm } from '../../components/ui';
import { examApi, type AssignmentDetail, type AssignmentMode, type RecipientState, type ReleasePolicy } from '../../lib/exam-api';
import { useI18n } from '../../lib/i18n';
import { toIso, useDateTime } from './list';
import './assignments.css';

/** An ISO instant as a datetime-local value in Vietnam time. */
const toLocal = (iso: string) => new Date(new Date(iso).getTime() + 7 * 3600_000).toISOString().slice(0, 16);

function EditForm({ row, onDone }: { row: AssignmentDetail; onDone: (saved: boolean) => void }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const [opens, setOpens] = useState(toLocal(row.opensAt));
  const [due, setDue] = useState(toLocal(row.dueAt));
  const [attempts, setAttempts] = useState(row.maxAttempts);
  const [mode, setMode] = useState<AssignmentMode>(row.mode);
  const [policy, setPolicy] = useState<ReleasePolicy>(row.releasePolicy);
  const [releaseAt, setReleaseAt] = useState(row.releaseAt ? toLocal(row.releaseAt) : '');
  const save = useMutation({
    mutationFn: (e: FormEvent) => {
      e.preventDefault();
      return examApi.updateAssignment(row.id, {
        opensAt: toIso(opens),
        dueAt: toIso(due),
        maxAttempts: attempts,
        mode,
        releasePolicy: policy,
        ...(policy === 'scheduled' ? { releaseAt: toIso(releaseAt) } : {}),
      });
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['assignments'] });
      client.invalidateQueries({ queryKey: ['assignment', row.id] });
      onDone(true);
    },
  });
  return (
    <form className="asg-form" onSubmit={(e) => save.mutate(e)} aria-label={t.assignments.editTitle}>
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
            <button key={n} type="button" className="secondary" aria-pressed={n === attempts} onClick={() => setAttempts(n)}>{n}</button>
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
          {(['after_submit', 'after_last', 'scheduled'] as const).map((p) => (
            <button key={p} type="button" className="secondary" aria-pressed={p === policy} onClick={() => setPolicy(p)}>{t.assignments.releaseOpts[p]}</button>
          ))}
        </span>
      </fieldset>
      {policy === 'scheduled' && (
        <label className="plain wide">{t.assignments.releaseAt}
          <input type="datetime-local" value={releaseAt} onChange={(e) => setReleaseAt(e.target.value)} required />
        </label>
      )}
      <div className="wide"><ErrorAlert error={save.error} /></div>
      <div className="row-actions end wide">
        <button type="button" className="secondary" onClick={() => onDone(false)}>{t.common.cancel}</button>
        <button type="submit" disabled={save.isPending}>{t.assignments.save}</button>
      </div>
    </form>
  );
}

export function AssignmentDetailPage() {
  const { id = '' } = useParams();
  const { t } = useI18n();
  const dateTime = useDateTime();
  const client = useQueryClient();
  const navigate = useNavigate();
  const { ask, dialog } = useConfirm();
  const [editing, setEditing] = useState(false);
  const [saved, setSaved] = useState(false);
  const detail = useQuery({ queryKey: ['assignment', id], queryFn: () => examApi.getAssignment(id) });
  const cancel = useMutation({
    mutationFn: () => examApi.cancelAssignment(id),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['assignments'] });
      client.invalidateQueries({ queryKey: ['assignment', id] });
    },
  });

  const row = detail.data;
  const students = row?.students ?? [];
  const count = (state: RecipientState) => students.filter((s) => s.state === state).length;
  const started = students.some((s) => s.attempts > 0);
  const locked = started || row?.status === 'cancelled';
  const stateLabel: Record<RecipientState, string> = { not_started: t.assignments.notStarted, in_progress: t.assignments.inProgress, submitted: t.assignments.submitted };

  return (
    <>
      <PageHead
        eyebrow={<Link to="/assignments" viewTransition>{t.assignments.back}</Link>}
        title={row?.testTitle ?? t.assignments.teacherTitle}
        lede={row ? t.assignments.detailMeta(row.className, t.assignments.status[row.status], dateTime(row.dueAt)) + (row.isTrial ? ` · ${t.assignments.trial}` : '') : undefined}
      >
        {row && (
          <button type="button" className="secondary" disabled={locked || editing} onClick={() => { setSaved(false); setEditing(true); }}>{t.assignments.edit}</button>
        )}
        {row && (
          <button
            type="button"
            className="secondary danger"
            disabled={locked || cancel.isPending}
            onClick={() => ask({ title: t.assignments.cancelAsk, body: t.assignments.cancelBody, action: t.assignments.cancel, run: () => cancel.mutate(undefined, { onSuccess: () => navigate('/assignments', { viewTransition: true }) }) })}
          >
            {t.assignments.cancel}
          </button>
        )}
      </PageHead>
      <section className="stack in" style={order(3)}>
        <ErrorAlert error={detail.error ?? cancel.error} />
        {detail.isPending && <Loading />}
        {row && (
          <>
            {saved && !editing && <Notice>{t.assignments.saved}</Notice>}
            {editing && !locked && <EditForm row={row} onDone={(ok) => { setEditing(false); setSaved(ok); }} />}
            {started && <p className="lock-note">{t.assignments.startedLock}</p>}
            <div className="stat-row">
              <div className="stat"><strong>{count('not_started')}</strong>{t.assignments.notStarted}</div>
              <div className="stat"><strong>{count('in_progress')}</strong>{t.assignments.inProgress}</div>
              <div className="stat dark"><strong>{count('submitted')}</strong>{t.assignments.submitted}</div>
            </div>
            <div className="table-scroll">
              <table className="asg-table">
                <thead>
                  <tr>
                    <th scope="col">{t.assignments.student}</th>
                    <th scope="col">{t.assignments.state}</th>
                    <th scope="col">{t.assignments.attemptsCol}</th>
                    <th scope="col">{t.assignments.note}</th>
                  </tr>
                </thead>
                <tbody>
                  {students.map((s) => (
                    <tr key={s.id}>
                      <td>{s.name}</td>
                      <td>{stateLabel[s.state]}</td>
                      <td>{s.attempts}/{row.maxAttempts}</td>
                      <td className="late">
                        {s.lateCount > 0 && <span>{t.assignments.late(s.lateCount)}</span>}
                        {s.late.map((l, i) => <span key={i} className="late-item">{t.assignments.lateItem(l.number, l.value)}</span>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {students.length === 0 && <p className="muted">{t.assignments.noStudents}</p>}
            </div>
          </>
        )}
      </section>
      {dialog}
    </>
  );
}
