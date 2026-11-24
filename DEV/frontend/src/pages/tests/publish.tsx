import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';
import { ErrorAlert, Icon, Loading, useConfirm } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { testsApi } from '../../lib/tests-api';
import { detailKey, useTest } from './editor';

type CheckId = 'pdf' | 'pages' | 'questions' | 'answers' | 'reviewed' | 'ielts';

/** Which blocker codes (from the server) make each checklist row fail. */
const BLOCKS: Record<CheckId, string[]> = {
  pdf: ['missing_pdf'],
  pages: ['no_sections', 'page_out_of_range'],
  questions: ['no_sections', 'question_gap', 'missing_type', 'incomplete_question'],
  answers: ['missing_answer'],
  reviewed: ['unreviewed_answer'],
  ielts: ['ielts_shape'],
};

export function PublishPage() {
  const { t } = useI18n();
  const { test } = useTest();
  const client = useQueryClient();
  const { ask, dialog } = useConfirm();
  const draft = test.status === 'draft';
  const readiness = useQuery({ queryKey: ['test', test.id, 'readiness'], queryFn: () => testsApi.readiness(test.id) });
  const done = () => {
    client.invalidateQueries({ queryKey: detailKey(test.id) });
    client.invalidateQueries({ queryKey: ['tests'] });
  };
  const publish = useMutation({
    mutationFn: () => testsApi.publish(test.id),
    onSuccess: done,
    // A refused publish means the checklist changed under us, so it is read again.
    onError: () => readiness.refetch(),
  });
  const archive = useMutation({ mutationFn: () => testsApi.archive(test.id), onSuccess: done });

  if (readiness.isPending) return <Loading />;
  if (readiness.isError) return <ErrorAlert error={readiness.error} />;
  const blockers = readiness.data.blockers;
  const ready = blockers.length === 0;
  const checks = (Object.keys(BLOCKS) as CheckId[]).filter((id) => (id !== 'ielts' || test.testType === 'ielts_standard') && (test.authoring === 'pdf' || (id !== 'pdf' && id !== 'pages')));
  const title = test.status === 'published' ? t.tests.publish.done : test.status === 'archived' ? t.tests.publish.archived : ready ? t.tests.publish.ready : t.tests.publish.blocked;

  return (
    <section className={`panel publish${draft && !ready ? ' is-blocked' : ''}`}>
      <h2 className="big">{title}</h2>
      {test.status === 'published' && <p className="muted">{t.tests.publish.doneBody}</p>}
      <ul className="checks">
        {checks.map((id) => {
          const pass = !BLOCKS[id].some((code) => blockers.includes(code));
          return (
            <li key={id}>
              <span>{t.tests.publish.checks[id]}</span>
              <span className={`tag${pass ? ' tag-live' : ' tag-bad'}`}>{pass ? t.tests.publish.ok : t.tests.publish.notOk}</span>
            </li>
          );
        })}
      </ul>
      <p className="muted">{t.tests.publish.note}</p>
      <ErrorAlert error={publish.error ?? archive.error} />
      <div className="row-actions end">
        <Link className="button-link auto secondary-link" to={`/tests/${test.id}/key`} viewTransition>
          <Icon name="back" />
          <span>{t.tests.publish.backKey}</span>
        </Link>
        {test.status === 'published' && (
          <button
            type="button"
            className="danger"
            disabled={archive.isPending}
            onClick={() => ask({ title: t.tests.publish.archiveAsk, body: t.tests.publish.archiveBody, action: t.tests.publish.archive, run: () => archive.mutate() })}
          >
            <Icon name="archive" />
            <span>{t.tests.publish.archive}</span>
          </button>
        )}
        {draft && (
          <button type="button" disabled={!ready || publish.isPending} onClick={() => publish.mutate()}>
            <span>{t.tests.publish.publish}</span>
          </button>
        )}
      </div>
      {dialog}
    </section>
  );
}
