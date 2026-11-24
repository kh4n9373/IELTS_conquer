import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { ErrorAlert, Icon, Loading, Notice } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { testsApi, type ExamContent } from '../../lib/tests-api';
import { detailKey, useTest } from './editor';
import { ImportPanel } from './import';

function Area({ label, value, onChange, disabled, rows = 3 }: { label: string; value: string; onChange: (v: string) => void; disabled: boolean; rows?: number }) {
  return (
    <label>
      <span className="muted">{label}</span>
      <textarea rows={rows} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/**
 * Step 2 for an imported test: fix the text. Numbers, types, options keys and blanks stay as imported;
 * changing those goes through re-import.
 */
export function ContentEditorPage() {
  const { t } = useI18n();
  const c = t.tests.content;
  const { test } = useTest();
  const client = useQueryClient();
  const locked = test.status !== 'draft';
  const query = useQuery({ queryKey: ['test-content', test.id], queryFn: () => testsApi.getContent(test.id) });
  const [draft, setDraft] = useState<ExamContent | null>(null);
  const [reimporting, setReimporting] = useState(false);
  useEffect(() => {
    if (query.data) setDraft(structuredClone(query.data.content));
  }, [query.data]);

  const save = useMutation({
    mutationFn: (content: ExamContent) => testsApi.putContent(test.id, content),
    onSuccess: (res) => {
      client.setQueryData(['test-content', test.id], res);
      client.invalidateQueries({ queryKey: detailKey(test.id) });
    },
  });

  if (query.isPending || !draft) return query.isError ? <ErrorAlert error={query.error} /> : <Loading />;
  const edit = (fn: (d: ExamContent) => void) => {
    const next = structuredClone(draft);
    fn(next);
    setDraft(next);
    save.reset();
  };

  if (reimporting) {
    return (
      <div className="content-editor">
        <button type="button" className="secondary" onClick={() => setReimporting(false)}>{t.common.cancel}</button>
        <ImportPanel testId={test.id} onDone={() => setReimporting(false)} />
      </div>
    );
  }

  return (
    <div className="content-editor">
      <p className="muted">{c.help}</p>
      {draft.sections.map((s, si) => (
        <fieldset key={si} disabled={locked}>
          <legend>{c.section(si + 1)}</legend>
          <Area label={c.sectionTitle} rows={1} value={s.title} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.title = v; })} />
          {s.passage.title !== null && (
            <Area label={c.passageTitle} rows={1} value={s.passage.title} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.passage.title = v; })} />
          )}
          {s.passage.paragraphs.map((p, pi) => (
            <Area key={pi} label={c.paragraph(p.label ?? String(pi + 1))} rows={4} value={p.text} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.passage.paragraphs[pi]!.text = v; })} />
          ))}
          {s.groups.map((g, gi) => {
            const nums = g.questions.map((q) => q.number);
            return (
              <fieldset key={gi}>
                <legend>{c.group(nums[0] ?? 0, nums[nums.length - 1] ?? 0, t.tests.questionTypes[g.type] ?? g.type)}</legend>
                <Area label={c.instructions} value={g.instructions} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.groups[gi]!.instructions = v; })} />
                {g.prompt !== null && (
                  <Area label={c.prompt} value={g.prompt} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.groups[gi]!.prompt = v; })} />
                )}
                {g.content !== null && (
                  <Area label={c.text} rows={5} value={g.content} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.groups[gi]!.content = v; })} />
                )}
                {g.options.map((o, oi) => (
                  <Area key={o.key} label={c.option(o.key)} rows={1} value={o.text} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.groups[gi]!.options[oi]!.text = v; })} />
                ))}
                {g.questions.map((q, qi) => (
                  <div key={q.number} className="stack">
                    {q.prompt !== null && (
                      <Area label={c.question(q.number)} rows={2} value={q.prompt} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.groups[gi]!.questions[qi]!.prompt = v; })} />
                    )}
                    {q.options?.map((o, oi) => (
                      <Area key={o.key} label={c.option(o.key)} rows={1} value={o.text} disabled={locked} onChange={(v) => edit((d) => { d.sections[si]!.groups[gi]!.questions[qi]!.options![oi]!.text = v; })} />
                    ))}
                  </div>
                ))}
              </fieldset>
            );
          })}
        </fieldset>
      ))}
      <ErrorAlert error={save.error} />
      {save.isSuccess && <Notice>{t.tests.form.saved}</Notice>}
      <span className="import-actions">
        {!locked && (
          <button type="button" className="secondary" onClick={() => setReimporting(true)}>{c.reimport}</button>
        )}
        {!locked && (
          <button type="button" className="secondary" disabled={save.isPending} onClick={() => save.mutate(draft)}>
            {save.isPending ? t.tests.form.working : t.tests.form.save}
          </button>
        )}
        <Link className="button-link auto" to={`/tests/${test.id}/key`} viewTransition>
          <span>{t.tests.next}</span>
          <Icon name="arrow" />
        </Link>
      </span>
    </div>
  );
}
