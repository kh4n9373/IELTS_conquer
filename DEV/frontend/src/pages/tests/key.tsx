import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';
import { Empty, ErrorAlert, Icon, Loading } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { testsApi, type KeyItem } from '../../lib/tests-api';
import { useTest } from './editor';

const keyQuery = (id: string) => ['test', id, 'key'] as const;
const readinessQuery = (id: string) => ['test', id, 'readiness'] as const;

/** Answers are edited as one text box: several accepted answers are separated by semicolons. */
const toAnswers = (text: string) => text.split(';').map((a) => a.trim()).filter(Boolean);

export function KeyPage() {
  const { t } = useI18n();
  const { test } = useTest();
  const client = useQueryClient();
  const locked = test.status !== 'draft';
  const key = useQuery({ queryKey: keyQuery(test.id), queryFn: () => testsApi.getKey(test.id) });

  const refresh = () => {
    client.invalidateQueries({ queryKey: keyQuery(test.id) });
    client.invalidateQueries({ queryKey: readinessQuery(test.id) });
  };
  const action = useMutation({
    mutationFn: async (run: () => Promise<unknown>) => run(),
    // Also after a refusal, so a changed answer is on screen before the teacher tries again.
    onSettled: () => refresh(),
  });
  const run = (work: () => Promise<unknown>) => action.mutate(work);

  const items = new Map<number, KeyItem>((key.data?.items ?? []).map((i) => [i.number, i]));
  const questions = test.sections.flatMap((s) => s.questions).sort((a, b) => a.number - b.number);
  const filled = (n: number) => (items.get(n)?.answers.length ?? 0) > 0;
  // A flagged row needs another look even if it was reviewed before, and publishing stays blocked.
  const isDone = (n: number) => Boolean(items.get(n)?.reviewed && !items.get(n)?.flagged && filled(n));
  const reviewed = questions.filter((q) => isDone(q.number)).length;
  const reviewable = questions.filter((q) => filled(q.number) && !items.get(q.number)?.reviewed && !items.get(q.number)?.flagged).map((q) => q.number);
  const reviewShown = (numbers: number[]) =>
    run(() => testsApi.reviewKey(test.id, numbers.map((number) => ({ number, answers: items.get(number)?.answers ?? [] }))));

  // What a row's box shows while it differs from the stored answer. Review always approves the stored
  // answer, so it stays off while any box differs or is being saved.
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [saving, setSaving] = useState(0);
  const setDraft = (number: number, text: string | null) =>
    setDrafts((d) => {
      const { [number]: _gone, ...rest } = d;
      return text === null ? rest : { ...rest, [number]: text };
    });
  const unsettled = saving > 0 || Object.keys(drafts).length > 0;

  const commitAnswer = (number: number, text: string) => {
    const answers = toAnswers(text);
    // An empty box or an unchanged answer goes back to what is stored. Answers cannot be cleared.
    if (answers.length === 0 || answers.join('; ') === (items.get(number)?.answers ?? []).join('; ')) return setDraft(number, null);
    setSaving((n) => n + 1);
    run(async () => {
      try {
        await testsApi.putKeyItems(test.id, { items: [{ number, answers }] });
        await client.invalidateQueries({ queryKey: keyQuery(test.id) });
      } finally {
        setSaving((n) => n - 1);
        setDraft(number, null);
      }
    });
  };
  if (key.isPending) return <Loading />;

  return (
    <section className="panel" aria-label={t.tests.key.area}>
        <div className="panel-head">
          <strong className="big">{t.tests.key.reviewed(reviewed, questions.length)}</strong>
          <button type="button" disabled={locked || reviewable.length === 0 || action.isPending || unsettled} onClick={() => reviewShown(reviewable)}>
            {t.tests.key.reviewClean}
          </button>
        </div>
        <ErrorAlert error={key.error ?? action.error} />
        {locked && <p className="muted">{t.tests.key.notEditable}</p>}
        {questions.length === 0 && <Empty title={t.tests.key.area}>{t.tests.key.empty}</Empty>}
        {questions.length > 0 && (
          <div className="table-scroll">
            <table className="key-table">
              <thead>
                <tr>
                  <th scope="col">{t.tests.key.no}</th>
                  <th scope="col">{t.tests.key.type}</th>
                  <th scope="col">{t.tests.key.answer}</th>
                  <th scope="col">{t.tests.key.source}</th>
                  <th scope="col">{t.tests.key.status}</th>
                </tr>
              </thead>
              <tbody>
                {questions.map((q) => {
                  const item = items.get(q.number);
                  const has = filled(q.number);
                  const done = isDone(q.number);
                  const status = done ? t.tests.key.done : !has ? t.tests.key.missing : item?.flagged ? t.tests.key.check : t.tests.key.review;
                  const value = (item?.answers ?? []).join('; ');
                  return (
                    <tr key={q.number} className={!has || item?.flagged ? 'is-flag' : undefined}>
                      {/* Locked rows have no enabled control, so the number stays reachable by keyboard. */}
                      <th scope="row" tabIndex={locked ? 0 : undefined}>{q.number}</th>
                      <td className="muted">{t.tests.questionTypes[q.questionType]}{q.wordLimit ? ` (${q.wordLimit})` : ''}</td>
                      <td>
                        <input
                          type="text"
                          aria-label={t.tests.key.answerFor(q.number)}
                          value={drafts[q.number] ?? value}
                          maxLength={400}
                          disabled={locked}
                          onChange={(e) => setDraft(q.number, e.target.value)}
                          onBlur={(e) => commitAnswer(q.number, e.target.value)}
                        />
                      </td>
                      <td className="muted">
                        {item && has ? `${t.tests.key.origin[item.origin]}${item.sourcePage ? `, ${t.tests.key.page(item.sourcePage)}` : ''}` : ''}
                        {item?.note && item.flagged && <span className="key-note"> · {t.tests.key.aiNote}: {item.note}</span>}
                      </td>
                      <td>
                        <button
                          type="button"
                          className={done ? 'review is-on' : 'secondary review'}
                          aria-label={t.tests.key.reviewLabel(q.number)}
                          aria-pressed={done}
                          disabled={locked || !has || done || action.isPending || unsettled}
                          onClick={() => reviewShown([q.number])}
                        >
                          {status}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="row-actions end">
          <Link className="button-link auto" to={`/tests/${test.id}/publish`} viewTransition>
            <span>{t.tests.next}</span>
            <Icon name="arrow" />
          </Link>
        </div>
    </section>
  );
}
