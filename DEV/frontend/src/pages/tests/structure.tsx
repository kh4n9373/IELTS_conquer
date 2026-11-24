import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { PdfViewer, pdfUrlKey } from '../../components/pdf-viewer';
import { Empty, ErrorAlert, Icon, Loading, Notice } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { GROUP_OF, QUESTION_TYPES, testsApi, type KeyItem, type QuestionType, type Section, type StructureSection } from '../../lib/tests-api';
import { detailKey, useTest } from './editor';
import { ImportPanel } from './import';

/** [2, 3, 5] becomes "2-3, 5". */
export function formatPages(pages: number[]): string {
  const out: string[] = [];
  for (let i = 0; i < pages.length; i++) {
    let end = i;
    while (end + 1 < pages.length && pages[end + 1] === pages[end]! + 1) end++;
    out.push(end > i + 1 ? `${pages[i]}-${pages[end]}` : pages.slice(i, end + 1).join(', '));
    i = end;
  }
  return out.join(', ');
}

/** "2-3, 5" becomes [2, 3, 5]. Anything that is not a page number gives null. */
export function parsePages(text: string): number[] | null {
  const pages: number[] = [];
  for (const part of text.split(/[,;\s]+/).filter(Boolean)) {
    const match = /^(\d{1,4})(?:-(\d{1,4}))?$/.exec(part);
    if (!match) return null;
    const first = Number(match[1]);
    const last = Number(match[2] ?? match[1]);
    if (first < 1 || last > 5000 || first > last || pages.length + (last - first + 1) > 50) return null;
    for (let p = first; p <= last; p++) pages.push(p);
  }
  return pages;
}

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const FIXED: Partial<Record<QuestionType, string[]>> = { tfng: ['TRUE', 'FALSE', 'NOT GIVEN'], ynng: ['YES', 'NO', 'NOT GIVEN'] };
/** The kinds teachers use most come first in the type list. */
const ORDER: QuestionType[] = ['sentence_completion', 'multiple_choice', 'multiple_choice_multi', 'summary_completion', ...QUESTION_TYPES.filter((q) => !['sentence_completion', 'multiple_choice', 'multiple_choice_multi', 'summary_completion'].includes(q))];
const isChoice = (type: QuestionType) => GROUP_OF[type] === 'multiple_choice';
const hasOptions = (type: QuestionType) => isChoice(type) || GROUP_OF[type] === 'matching' || type === 'summary_completion';
const letters = (answer: string) => answer.toUpperCase().match(/[A-Z]/g) ?? [];

/** `answer` is the text in the answer box: "B", "A, C", "TRUE", or variants split by ";". */
/** `group` is the uid of the first question of a "choose two" set, graded together in either order. */
type DraftQ = { uid: number; number: number; type: QuestionType; prompt: string; options: string[]; answer: string; wordLimit: number | null; group?: number | null };
type DraftSection = { uid: number; title: string; body: string; passage: string; pages: string; minutes: number | null; unitsPrimary: string[]; unitsSecondary: string[]; qs: DraftQ[] };

let uidSeq = 0;
const next = () => ++uidSeq;
/** Edits not saved yet, per test, so moving to another step and back keeps them.
 * shortcut: lost on a page reload, move to sessionStorage if that bites. */
const unsaved = new Map<string, DraftSection[]>();

/** Points each grouped question at its group's first question by uid, so renumbering keeps the set. */
function withGroups(qs: DraftQ[], groups: (number | null)[]): DraftQ[] {
  const uidOf = new Map(qs.map((q) => [q.number, q.uid]));
  return qs.map((q, i) => ({ ...q, group: uidOf.get(groups[i] ?? -1) ?? null }));
}

function fromServer(sections: Section[], items: KeyItem[]): DraftSection[] {
  return sections.map((s) => ({
    uid: next(),
    title: s.title,
    body: s.body,
    passage: formatPages(s.passagePages),
    pages: formatPages(s.questionPages),
    minutes: s.suggestedMinutes,
    unitsPrimary: s.unitsPrimary,
    unitsSecondary: s.unitsSecondary,
    qs: withGroups(s.questions.map((q) => {
      const answers = items.find((i) => i.number === q.number)?.answers ?? [];
      return { uid: next(), number: q.number, type: q.questionType, prompt: q.prompt, options: q.options, wordLimit: q.wordLimit, answer: isChoice(q.questionType) ? answers[0] ?? '' : answers.join('; ') };
    }), s.questions.map((q) => q.answerGroup ?? null)),
  }));
}

function keyAnswers(q: DraftQ): string[] {
  if (isChoice(q.type)) {
    const picked = [...new Set(letters(q.answer))].sort();
    if (picked.length === 0) return [];
    return q.type === 'multiple_choice' ? [picked[0] as string] : [picked.join(', ')];
  }
  return q.answer.split(';').map((a) => a.trim()).filter(Boolean);
}

/** One page for both ways of making a test: what changes is the left pane (PDF or passage text). */
export function StructurePage() {
  const { test } = useTest();
  if (test.authoring === 'pdf' && test.status === 'draft') {
    if (!test.studentPdf) return <PdfUpload />;
    if (test.ocr && ['queued', 'running'].includes(test.ocr.state)) return <Processing />;
  }
  if (test.authoring === 'manual' && test.status === 'draft') return <ManualWithReimport />;
  return <QuestionsLoader />;
}

/** A manual draft can be replaced wholesale from AI JSON; the editor reopens on the new questions. */
function ManualWithReimport() {
  const { t } = useI18n();
  const { test } = useTest();
  const [open, setOpen] = useState(false);
  const [round, setRound] = useState(0);
  return (
    <>
      <details className="reimport" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary>{t.tests.mode.reimport}</summary>
        <p className="muted">{t.tests.mode.reimportHelp}</p>
        {open && (
          <ImportPanel
            testId={test.id}
            onDone={() => {
              unsaved.delete(test.id);
              setOpen(false);
              setRound((n) => n + 1);
            }}
          />
        )}
      </details>
      <QuestionsLoader key={round} />
    </>
  );
}

function PdfUpload({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const p = t.tests.pdfFlow;
  const { test } = useTest();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const send = useMutation({
    mutationFn: (chosen: File) => testsApi.uploadPdf(test.id, chosen, true),
    onSuccess: () => {
      setFile(null);
      client.invalidateQueries({ queryKey: detailKey(test.id) });
      client.invalidateQueries({ queryKey: pdfUrlKey(test.id) });
      client.invalidateQueries({ queryKey: ['tests'] });
    },
  });
  if (compact)
    return (
      <label className="button-link auto file-button">
        <span>{send.isPending ? p.uploading : p.replace}</span>
        <input type="file" accept="application/pdf" disabled={send.isPending} onChange={(e) => { const f = e.target.files?.[0]; if (f) send.mutate(f); e.target.value = ''; }} />
      </label>
    );
  return (
    <section className="panel pdf-upload">
      <h2>{p.title}</h2>
      <div className={`drop${send.isError ? ' is-bad' : ''}`}>
        <strong>{file ? file.name : t.tests.form.pdfNone}</strong>
        <input aria-label={t.tests.form.pdf} type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </div>
      <p className="muted">{p.consent}</p>
      <ErrorAlert error={send.error} />
      {send.isError && <p className="muted">{t.tests.form.uploadHelp}</p>}
      <button className="primary-wide" disabled={!file || send.isPending} onClick={() => file && send.mutate(file)}>
        <span>{send.isPending ? p.uploading : p.send}</span>
        <Icon name="arrow" />
      </button>
    </section>
  );
}

function Processing() {
  const { t } = useI18n();
  return (
    <section className="panel processing" role="status">
      <span className="tag">{t.tests.pdfFlow.processing}</span>
      <h2>{t.tests.pdfFlow.processingTitle}</h2>
      <p className="muted">{t.tests.pdfFlow.processingBody}</p>
      <Loading />
    </section>
  );
}

/** Loads the current answers once, so every question opens with its answer beside it. */
function QuestionsLoader() {
  const { test } = useTest();
  const key = useQuery({ queryKey: ['test', test.id, 'key'], queryFn: () => testsApi.getKey(test.id) });
  // Coming back from another step waits for fresh answers, so the editor never opens on stale ones.
  if (key.isPending || (key.isFetching && !key.isFetchedAfterMount)) return <Loading />;
  if (key.isError) return <ErrorAlert error={key.error} />;
  return <QuestionsEditor sections={test.sections} items={key.data.items} />;
}

function QuestionsEditor({ sections, items }: { sections: Section[]; items: KeyItem[] }) {
  const { t } = useI18n();
  const m = t.tests.manual;
  const { test } = useTest();
  const pdf = test.authoring === 'pdf';
  const client = useQueryClient();
  const navigate = useNavigate();
  const locked = test.status !== 'draft';
  const [drafts, setDrafts] = useState<DraftSection[]>(() => unsaved.get(test.id) ?? fromServer(sections, items));
  const [open, setOpen] = useState(0);
  const [dirty, setDirty] = useState(() => unsaved.has(test.id));
  const [badPages, setBadPages] = useState(false);
  const [pdfPage, setPdfPage] = useState(() => sections[0]?.passagePages[0] ?? 1);
  const ocrFailed = pdf && test.ocr?.state === 'failed';

  const build = (list: DraftSection[]): StructureSection[] | null => {
    let counter = 0;
    const out: StructureSection[] = [];
    for (const d of list) {
      const passagePages = parsePages(d.passage);
      const questionPages = parsePages(d.pages);
      if (passagePages === null || questionPages === null) return null;
      // A PDF keeps its own numbering; a typed test is numbered in order.
      const numbered = d.qs.map((q) => ({ q, number: pdf ? q.number : ++counter }));
      const numOf = new Map(numbered.map(({ q, number }) => [q.uid, number]));
      out.push({
        title: d.title.trim() || m.section(out.length + 1),
        body: d.body,
        suggestedMinutes: d.minutes,
        passagePages,
        questionPages,
        unitsPrimary: d.unitsPrimary,
        unitsSecondary: d.unitsSecondary,
        ranges: [],
        questions: numbered.map(({ q, number }) => {
          const lead = q.group != null && q.type === 'multiple_choice' ? numOf.get(q.group) : undefined;
          return {
            number,
            questionType: q.type,
            prompt: q.prompt.trim(),
            options: hasOptions(q.type) ? q.options.map((o) => o.trim()).filter(Boolean) : [],
            ...(GROUP_OF[q.type] === 'completion' && q.wordLimit ? { wordLimit: q.wordLimit } : {}),
            ...(lead !== undefined ? { answerGroup: lead } : {}),
            answers: keyAnswers(q),
          };
        }),
      });
    }
    return out;
  };

  const latest = useRef(drafts);
  latest.current = drafts;
  const save = useMutation({
    mutationFn: async (list: DraftSection[]) => {
      const built = build(list);
      setBadPages(built === null);
      if (built === null) throw new Error(m.badPages);
      const saved = await testsApi.putStructure(test.id, built);
      client.setQueryData(detailKey(test.id), (old: typeof test | undefined) => (old ? { ...old, sections: saved.sections } : old));
      client.invalidateQueries({ queryKey: ['test', test.id] });
      if (unsaved.get(test.id) === list) unsaved.delete(test.id);
      if (latest.current === list) setDirty(false);
    },
  });

  // Every edit is saved a moment after typing stops; a failed save waits for the next edit.
  useEffect(() => {
    if (!dirty || locked) return;
    unsaved.set(test.id, drafts);
    if (save.isPending || (save.isError && save.variables === drafts)) return;
    const timer = setTimeout(() => save.mutate(drafts), 800);
    return () => clearTimeout(timer);
  }, [drafts, dirty, locked, save, test.id]);
  // Leaving the step saves what the timer has not saved yet.
  const flush = useRef(() => {});
  flush.current = () => {
    if (dirty && !locked && save.variables !== drafts) save.mutate(drafts);
  };
  useEffect(() => () => flush.current(), []);

  const edit = (change: (list: DraftSection[]) => DraftSection[]) => {
    setDrafts(change);
    setDirty(true);
  };
  const current = drafts[Math.min(open, drafts.length - 1)];
  const patchSection = (change: Partial<DraftSection>) => current && edit((list) => list.map((x) => (x.uid === current.uid ? { ...x, ...change } : x)));
  const patchQ = (q: number, change: Partial<DraftQ>) =>
    current && edit((list) => list.map((x) => (x.uid === current.uid ? { ...x, qs: x.qs.map((y) => (y.uid === q ? { ...y, ...change } : y)) } : x)));
  const addSection = () => {
    edit((list) => [...list, { uid: next(), title: m.section(list.length + 1), body: '', passage: '', pages: '', minutes: null, unitsPrimary: [], unitsSecondary: [], qs: [] }]);
    setOpen(drafts.length);
  };
  const addQuestion = () => {
    if (!current) return;
    // A new question starts like the one above it, so a word bank or option set is not typed twice.
    const last = current.qs.at(-1);
    const type = last?.type ?? 'multiple_choice';
    const top = Math.max(0, ...drafts.flatMap((d) => d.qs.map((q) => q.number)));
    const options = last && hasOptions(type) ? last.options : hasOptions(type) ? ['', '', '', ''] : [];
    patchSection({ qs: [...current.qs, { uid: next(), number: top + 1, type, prompt: '', options, answer: '', wordLimit: last?.wordLimit ?? null }] });
  };
  const pick = (index: number) => {
    setOpen(index);
    const first = parsePages(drafts[index]?.passage ?? '')?.[0];
    if (first) setPdfPage(first);
  };
  const goOn = async () => {
    if (dirty && !locked && !(await save.mutateAsync(drafts).then(() => true, () => false))) return;
    navigate(`/tests/${test.id}/key`);
  };
  const total = drafts.reduce((n, d) => n + d.qs.length, 0);
  let shown = drafts.slice(0, open).reduce((n, d) => n + d.qs.length, 0);

  return (
    <div className="qe">
      <div className="qe-left">
        {pdf ? (
          <PdfViewer testId={test.id} hasPdf={test.studentPdf !== null} page={pdfPage} onPageChange={setPdfPage} />
        ) : current ? (
          <section className="panel" aria-label={m.content}>
            <fieldset className="stack" disabled={locked}>
              <label className="plain">{m.sectionTitle}
                <input type="text" value={current.title} maxLength={120} onChange={(e) => patchSection({ title: e.target.value })} />
              </label>
              <label className="plain">{m.body}
                <textarea rows={16} value={current.body} maxLength={50000} placeholder={m.bodyHint} onChange={(e) => patchSection({ body: e.target.value })} />
              </label>
            </fieldset>
          </section>
        ) : (
          <Empty title={m.area}>{m.empty}</Empty>
        )}
      </div>
      <section className="panel qe-right" aria-label={m.area}>
        {ocrFailed && (
          <div className="alert" role="alert">
            <span>{t.tests.pdfFlow.failed}</span>
            {!locked && <button type="button" className="secondary" onClick={() => void testsApi.retryOcr(test.id).then(() => client.invalidateQueries({ queryKey: detailKey(test.id) }))}>{t.tests.pdfFlow.retry}</button>}
          </div>
        )}
        <div className="qe-tabs" role="tablist" aria-label={m.area}>
          {drafts.map((d, i) => (
            <button key={d.uid} type="button" role="tab" aria-selected={i === open} className={`xb${i === open ? ' is-on' : ''}`} onClick={() => pick(i)}>{d.title || m.section(i + 1)}</button>
          ))}
          {!locked && <button type="button" className="secondary" onClick={addSection}><Icon name="plus" /><span>{m.addSection}</span></button>}
          {pdf && !locked && <PdfUpload compact />}
        </div>
        {current && (
          <fieldset className="stack" disabled={locked}>
            <div className="qe-head">
              {pdf && (
                <>
                  <label className="plain">{m.sectionTitle}
                    <input type="text" value={current.title} maxLength={120} onChange={(e) => patchSection({ title: e.target.value })} />
                  </label>
                  <label className="plain">{t.tests.structure.passagePages}
                    <input type="text" value={current.passage} placeholder={t.tests.structure.pagesHint} onChange={(e) => patchSection({ passage: e.target.value })} />
                  </label>
                  <label className="plain">{t.tests.structure.questionPages}
                    <input type="text" value={current.pages} placeholder={t.tests.structure.pagesHint} onChange={(e) => patchSection({ pages: e.target.value })} />
                  </label>
                </>
              )}
            </div>
            {current.qs.map((q) => {
              const n = pdf ? q.number : ++shown;
              const picked = new Set(letters(q.answer));
              const fixed = FIXED[q.type];
              const bank = q.type === 'summary_completion' ? q.options.map((o) => o.trim()).filter(Boolean) : [];
              const optionLabel = q.type === 'summary_completion' ? m.bank : m.options;
              const toggle = (letter: string) => {
                if (q.type === 'multiple_choice') return patchQ(q.uid, { answer: letter });
                const set = new Set(picked);
                if (set.has(letter)) set.delete(letter);
                else set.add(letter);
                patchQ(q.uid, { answer: [...set].sort().join(', ') });
              };
              return (
                <div key={q.uid} className="qrow" role="group" aria-label={m.question(n)}>
                  <div className="mq-head">
                    <strong>{m.question(n)}</strong>
                    <select aria-label={m.kind} value={q.type} onChange={(e) => {
                      const type = e.target.value as QuestionType;
                      patchQ(q.uid, { type, answer: '', options: hasOptions(type) ? (q.options.length ? q.options : ['', '', '', '']) : [] });
                    }}>
                      {ORDER.map((k) => <option key={k} value={k}>{t.tests.questionTypes[k]}</option>)}
                    </select>
                    {!locked && <button type="button" className="danger ghost" onClick={() => patchSection({ qs: current.qs.filter((x) => x.uid !== q.uid) })}>{m.removeQuestion}</button>}
                  </div>
                  <label className="plain">{m.prompt}
                    <textarea rows={2} value={q.prompt} maxLength={2000} placeholder={GROUP_OF[q.type] === 'completion' ? m.promptHint : undefined} onChange={(e) => patchQ(q.uid, { prompt: e.target.value })} />
                  </label>
                  {hasOptions(q.type) && (
                    <div className="mq-options" role="group" aria-label={optionLabel}>
                      <span className="mq-label">{optionLabel}{isChoice(q.type) && <span className="muted"> · {m.pickHint}</span>}</span>
                      {q.options.map((o, i) => {
                        const letter = LETTERS[i] as string;
                        return (
                          <div key={i} className="mq-option">
                            {isChoice(q.type) ? (
                              <button type="button" className={`xb mq-pick${picked.has(letter) ? ' is-on' : ''}`} aria-pressed={picked.has(letter)} aria-label={m.correct(letter)} onClick={() => toggle(letter)}>{letter}</button>
                            ) : (
                              <span className="mq-letter">{q.type === 'summary_completion' ? i + 1 : letter}</span>
                            )}
                            <input type="text" value={o} maxLength={300} aria-label={`${optionLabel} ${q.type === 'summary_completion' ? i + 1 : letter}`}
                              onChange={(e) => patchQ(q.uid, { options: q.options.map((x, j) => (j === i ? e.target.value : x)) })} />
                            {!locked && <button type="button" className="ghost mq-remove" aria-label={m.removeOption} onClick={() => patchQ(q.uid, { options: q.options.filter((_, j) => j !== i), answer: isChoice(q.type) ? '' : q.answer })}>×</button>}
                          </div>
                        );
                      })}
                      {!locked && q.options.length < 26 && <button type="button" className="secondary" onClick={() => patchQ(q.uid, { options: [...q.options, ''] })}><Icon name="plus" /><span>{m.addOption}</span></button>}
                    </div>
                  )}
                  {!isChoice(q.type) && (
                    <div className="mq-answer">
                      <label className="plain">{m.answer}
                        {fixed || bank.length > 0 ? (
                          <select aria-label={m.answerOf(n)} value={q.answer} onChange={(e) => patchQ(q.uid, { answer: e.target.value })}>
                            <option value="">—</option>
                            {(fixed ?? bank).map((o) => <option key={o} value={o}>{o}</option>)}
                          </select>
                        ) : (
                          <input type="text" aria-label={m.answerOf(n)} value={q.answer} placeholder={m.answerHint} onChange={(e) => patchQ(q.uid, { answer: e.target.value })} />
                        )}
                      </label>
                      {GROUP_OF[q.type] === 'completion' && (
                        <label className="plain small">{t.tests.structure.wordLimit}
                          <input type="number" min={1} max={20} value={q.wordLimit ?? ''} onChange={(e) => patchQ(q.uid, { wordLimit: e.target.value ? Math.min(20, Math.max(1, Number(e.target.value))) : null })} />
                        </label>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {!locked && (
              <div className="row-actions">
                <button type="button" className="secondary" onClick={addQuestion}><Icon name="plus" /><span>{m.addQuestion}</span></button>
                <button type="button" className="danger ghost" onClick={() => { edit((list) => list.filter((x) => x.uid !== current.uid)); setOpen(0); }}>{m.removeSection}</button>
              </div>
            )}
          </fieldset>
        )}
        {drafts.length === 0 && <Empty title={m.area}>{pdf ? m.emptyPdf : m.empty}</Empty>}
        <p className="total"><strong>{m.total(drafts.length, total)}</strong></p>
        {badPages && <p className="alert" role="alert"><span>{m.badPages}</span></p>}
        {!badPages && <ErrorAlert error={save.error} />}
        {!locked && dirty && !save.isError && <p className="muted" role="status">{m.saving}</p>}
        {save.isSuccess && !dirty && <Notice>{m.saved}</Notice>}
        {locked && <p className="muted">{t.tests.locked}</p>}
        <div className="row-actions end">
          <button type="button" disabled={save.isPending} onClick={() => void goOn()}><span>{t.tests.next}</span><Icon name="arrow" /></button>
        </div>
      </section>
    </div>
  );
}
