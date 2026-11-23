import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useParams } from 'react-router';
import { ErrorAlert, Loading, order, PageHead, useConfirm } from '../../components/ui';
import { examApi, type RecipientState } from '../../lib/exam-api';
import { useI18n } from '../../lib/i18n';
import { useDateTime } from './list';
import './assignments.css';

export function AssignmentDetailPage() {
  const { id = '' } = useParams();
  const { t } = useI18n();
  const dateTime = useDateTime();
  const client = useQueryClient();
  const navigate = useNavigate();
  const { ask, dialog } = useConfirm();
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
        lede={row ? t.assignments.detailMeta(row.className, t.assignments.status[row.status], dateTime(row.dueAt)) : undefined}
      >
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
                      <td className="late">{s.lateCount > 0 ? t.assignments.late(s.lateCount) : ''}</td>
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
