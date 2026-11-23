import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';
import { Empty, ErrorAlert, Icon, Loading, Notice } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { testsApi, type ImportCounts, type KeyItem } from '../../lib/tests-api';
import { useTest } from './editor';
import { KeyRegion } from './key-region';

const keyQuery = (id: string) => ['test', id, 'key'] as const;
const readinessQuery = (id: string) => ['test', id, 'readiness'] as const;

/** Answers are edited as one text box: several accepted answers are separated by semicolons. */
const toAnswers = (text: string) => text.split(';').map((a) => a.trim()).filter(Boolean);

export function KeyPage() {
  const { t } = useI18n();
  const { test } = useTest();
  const client = useQueryClient();
  const locked = test.status !== 'draft';
  const key = useQuery({
    queryKey: keyQuery(test.id),
    queryFn: () => testsApi.getKey(test.id),
    // A running scan fills the table by itself, so the page keeps checking until it ends.
    refetchInterval: (q) => (['queued', 'running'].includes(q.state.data?.scan?.state ?? '') ? 3000 : false),
  });
  const [pasted, setPasted] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [uploading, setUploading] = useState(0);

  const refresh = () => {
    client.invalidateQueries({ queryKey: keyQuery(test.id) });
    client.invalidateQueries({ queryKey: readinessQuery(test.id) });
  };
  const action = useMutation({
    mutationFn: async (run: () => Promise<unknown>) => run(),
    onMutate: () => setMessage(null),
    onSuccess: () => refresh(),
  });
  const run = (work: () => Promise<unknown>) => action.mutate(work);

  const items = new Map<number, KeyItem>((key.data?.items ?? []).map((i) => [i.number, i]));
  const questions = test.sections.flatMap((s) => s.questions).sort((a, b) => a.number - b.number);
  const filled = (n: number) => (items.get(n)?.answers.length ?? 0) > 0;
  const reviewed = questions.filter((q) => items.get(q.number)?.reviewed && filled(q.number)).length;
  const reviewable = questions.filter((q) => filled(q.number) && !items.get(q.number)?.reviewed && !items.get(q.number)?.flagged).map((q) => q.number);
  const scan = key.data?.scan ?? null;
  const source = key.data?.source ?? null;
  const selectedItem = selected === null ? undefined : items.get(selected);

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
  const onFile = (file: File | undefined) => {
    if (!file) return;
    setUploading((n) => n + 1);
    run(async () => {
      try {
        const info = await testsApi.uploadKeySource(test.id, file);
        if (info.hasTextLayer) {
          const counts: ImportCounts = await testsApi.importKey(test.id);
          setMessage(t.tests.key.imported(counts));
        }
      } finally {
        // The rows must show the new file's regions before the card may draw again.
        await client.invalidateQueries({ queryKey: keyQuery(test.id) });
        setUploading((n) => n - 1);
      }
    });
  };

  if (key.isPending) return <Loading />;

  return (
    <div className="workbench key-bench">
      <section className="panel" aria-label={t.tests.key.area}>
        <div className="panel-head">
          <strong className="big">{t.tests.key.reviewed(reviewed, questions.length)}</strong>
          <button type="button" disabled={locked || reviewable.length === 0 || action.isPending || unsettled} onClick={() => run(() => testsApi.reviewKey(test.id, reviewable))}>
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
                  const status = item?.reviewed && has ? t.tests.key.done : !has ? t.tests.key.missing : item?.flagged ? t.tests.key.check : t.tests.key.review;
                  const value = (item?.answers ?? []).join('; ');
                  return (
                    <tr
                      key={q.number}
                      className={!has || item?.flagged ? 'is-flag' : undefined}
                      onClick={() => setSelected(q.number)}
                      onFocusCapture={() => setSelected(q.number)}
                    >
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
                      </td>
                      <td>
                        <button
                          type="button"
                          className={item?.reviewed && has ? 'review is-on' : 'secondary review'}
                          aria-label={t.tests.key.reviewLabel(q.number)}
                          aria-pressed={Boolean(item?.reviewed && has)}
                          disabled={locked || !has || Boolean(item?.reviewed) || action.isPending || unsettled}
                          onClick={() => run(() => testsApi.reviewKey(test.id, [q.number]))}
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
      <aside className="panel" aria-label={t.tests.key.input}>
        <h2>{t.tests.key.input}</h2>
        <fieldset className="stack" disabled={locked}>
          <label className="plain">{t.tests.key.pasteLabel}
            <textarea rows={5} value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder={'1. A\n2. C\n23. two; platform two'} />
          </label>
          <p className="muted">{t.tests.key.pasteHint}</p>
          <button
            type="button"
            className="secondary"
            disabled={pasted.trim() === '' || action.isPending}
            onClick={() => run(async () => {
              const data = await testsApi.putKeyItems(test.id, { pasted });
              setPasted('');
              setMessage(t.tests.key.pasted(data.items.length));
            })}
          >
            {t.tests.key.pasteApply}
          </button>
          <label className="plain">{t.tests.key.file}
            <input type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => { onFile(e.target.files?.[0]); e.target.value = ''; }} />
          </label>
          <p className="muted">{t.tests.key.fileNote}</p>
          <KeyRegion
            key={source?.revision ?? 'none'}
            testId={test.id}
            revision={source?.revision ?? null}
            number={uploading > 0 ? null : selected}
            page={uploading > 0 || !source ? null : selectedItem?.sourcePage ?? null}
            bbox={uploading > 0 || !source ? null : selectedItem?.sourceBbox ?? null}
            onFileChanged={() => client.invalidateQueries({ queryKey: keyQuery(test.id) })}
          />
          {source && !source.hasTextLayer && (
            <div className="stack">
              <p className="muted">{t.tests.key.scanReady}</p>
              <button type="button" className="secondary" disabled={action.isPending} onClick={() => run(() => testsApi.scanKey(test.id))}>{t.tests.key.scan}</button>
            </div>
          )}
        </fieldset>
        {message && <Notice>{message}</Notice>}
        {scan && (
          <div role="status" className="scan-status">
            <span>{t.tests.key.scanState[scan.state]}</span>
            {scan.state === 'failed' && !locked && (
              <button type="button" className="secondary" disabled={action.isPending} onClick={() => run(() => testsApi.scanKey(test.id))}>{t.tests.key.retryScan}</button>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}
