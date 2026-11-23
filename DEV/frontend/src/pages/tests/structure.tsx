import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { PdfViewer } from '../../components/pdf-viewer';
import { Empty, ErrorAlert, Icon, Notice } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { GROUP_OF, QUESTION_TYPES, testsApi, type QuestionGroup, type QuestionType, type Section, type StructureSection } from '../../lib/tests-api';
import { detailKey, useTest } from './editor';

type DraftRange = { uid: number; from: string; to: string; questionType: QuestionType; wordLimit: string };
type DraftSection = {
  uid: number;
  title: string;
  passage: string;
  questions: string;
  suggestedMinutes: number | null;
  unitsPrimary: string[];
  unitsSecondary: string[];
  ranges: DraftRange[];
};

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

const whole = (text: string) => (/^\d{1,3}$/.test(text.trim()) ? Number(text) : null);

function toStructure(drafts: DraftSection[]): { sections: StructureSection[] } | 'pages' | 'range' {
  const sections: StructureSection[] = [];
  for (const d of drafts) {
    const passagePages = parsePages(d.passage);
    const questionPages = parsePages(d.questions);
    if (passagePages === null || questionPages === null) return 'pages';
    const ranges: StructureSection['ranges'] = [];
    for (const r of d.ranges) {
      const from = whole(r.from);
      const to = whole(r.to);
      const limit = r.wordLimit.trim() === '' ? undefined : whole(r.wordLimit);
      const allowsLimit = GROUP_OF[r.questionType] === 'completion';
      if (from === null || to === null || from < 1 || to > 200 || from > to || limit === null || (limit !== undefined && (limit < 1 || limit > 20))) return 'range';
      ranges.push({ from, to, questionType: r.questionType, ...(allowsLimit && limit !== undefined ? { wordLimit: limit } : {}) });
    }
    sections.push({ title: d.title.trim(), suggestedMinutes: d.suggestedMinutes, passagePages, questionPages, unitsPrimary: d.unitsPrimary, unitsSecondary: d.unitsSecondary, ranges });
  }
  return { sections };
}

export function StructurePage() {
  const { t } = useI18n();
  const { test } = useTest();
  const client = useQueryClient();
  const navigate = useNavigate();
  const uid = useRef(0);
  const next = () => ++uid.current;
  const fromServer = (sections: Section[]): DraftSection[] =>
    sections.map((s) => ({
      uid: next(),
      title: s.title,
      passage: formatPages(s.passagePages),
      questions: formatPages(s.questionPages),
      suggestedMinutes: s.suggestedMinutes,
      unitsPrimary: s.unitsPrimary,
      unitsSecondary: s.unitsSecondary,
      ranges: s.ranges.map((r) => ({ uid: next(), from: String(r.from), to: String(r.to), questionType: r.questionType, wordLimit: r.wordLimit === null ? '' : String(r.wordLimit) })),
    }));
  const [drafts, setDrafts] = useState(() => fromServer(test.sections));
  const [dirty, setDirty] = useState(false);
  const [problem, setProblem] = useState<'pages' | 'range' | null>(null);
  const locked = test.status !== 'draft';
  const ocr = test.ocr ?? null;

  const save = useMutation({
    mutationFn: async (): Promise<boolean> => {
      const built = toStructure(drafts);
      if (typeof built === 'string') {
        setProblem(built);
        return false;
      }
      setProblem(null);
      const saved = await testsApi.putStructure(test.id, built.sections);
      client.setQueryData(detailKey(test.id), (old: typeof test | undefined) => (old ? { ...old, sections: saved.sections } : old));
      client.invalidateQueries({ queryKey: detailKey(test.id) });
      client.invalidateQueries({ queryKey: ['test', test.id, 'readiness'] });
      setDirty(false);
      return true;
    },
  });
  const retry = useMutation({
    mutationFn: () => testsApi.retryOcr(test.id),
    onSuccess: () => client.invalidateQueries({ queryKey: detailKey(test.id) }),
  });

  const edit = (change: (list: DraftSection[]) => DraftSection[]) => {
    setDrafts(change);
    setDirty(true);
    save.reset();
  };
  const patch = (uidOf: number, change: Partial<DraftSection>) => edit((list) => list.map((s) => (s.uid === uidOf ? { ...s, ...change } : s)));
  const patchRange = (sectionUid: number, rangeUid: number, change: Partial<DraftRange>) =>
    edit((list) => list.map((s) => (s.uid === sectionUid ? { ...s, ranges: s.ranges.map((r) => (r.uid === rangeUid ? { ...r, ...change } : r)) } : s)));
  const addSection = () =>
    edit((list) => [...list, { uid: next(), title: t.tests.structure.section(list.length + 1), passage: '', questions: '', suggestedMinutes: null, unitsPrimary: [], unitsSecondary: [], ranges: [] }]);
  const addRange = (s: DraftSection) => {
    const all = drafts.flatMap((d) => d.ranges);
    const top = Math.max(0, ...all.map((r) => whole(r.to) ?? 0));
    patch(s.uid, { ranges: [...s.ranges, { uid: next(), from: String(top + 1), to: String(top + 1), questionType: 'multiple_choice', wordLimit: '' }] });
  };

  const counts: Record<QuestionGroup, number> = { matching: 0, completion: 0, multiple_choice: 0, tfng: 0 };
  let total = 0;
  for (const s of drafts) {
    for (const r of s.ranges) {
      const from = whole(r.from);
      const to = whole(r.to);
      if (from === null || to === null || from > to) continue;
      counts[GROUP_OF[r.questionType]] += to - from + 1;
      total += to - from + 1;
    }
  }

  const goOn = async () => {
    if (dirty && !locked && !(await save.mutateAsync().catch(() => false))) return;
    navigate(`/tests/${test.id}/key`);
  };
  const chip = ocr && (
    <span className={`tag${ocr.state === 'failed' ? ' tag-bad' : ocr.state === 'done' ? ' tag-live' : ''}`}>{t.tests.ocr[ocr.state]}</span>
  );

  return (
    <div className="workbench">
      <PdfViewer
        testId={test.id}
        hasPdf={test.studentPdf !== null}
        label={chip}
        footer={ocr?.state === 'failed' && (
          <div className="ocr-failed" role="status">
            <span>{t.tests.ocr.failedHelp}</span>
            <button type="button" className="secondary" onClick={() => retry.mutate()} disabled={retry.isPending || test.status === 'archived'}>{t.tests.ocr.retry}</button>
            <ErrorAlert error={retry.error} />
          </div>
        )}
      />
      <section className="panel" aria-label={t.tests.structure.area}>
        <fieldset className="stack" disabled={locked}>
          {drafts.length === 0 && <Empty title={t.tests.structure.area}>{t.tests.structure.empty}</Empty>}
          {drafts.map((s, index) => (
            <fieldset key={s.uid} className="passage">
              <legend>{t.tests.structure.section(index + 1)}</legend>
              <div className="pair">
                <label className="plain">{t.tests.structure.sectionTitle}
                  <input type="text" value={s.title} maxLength={120} onChange={(e) => patch(s.uid, { title: e.target.value })} />
                </label>
                <label className="plain">{t.tests.structure.passagePages}
                  <input type="text" value={s.passage} placeholder={t.tests.structure.pagesHint} onChange={(e) => patch(s.uid, { passage: e.target.value })} />
                </label>
                <label className="plain">{t.tests.structure.questionPages}
                  <input type="text" value={s.questions} placeholder={t.tests.structure.pagesHint} onChange={(e) => patch(s.uid, { questions: e.target.value })} />
                </label>
              </div>
              {s.ranges.map((r, i) => (
                <div key={r.uid} className="range" role="group" aria-label={t.tests.structure.range(i + 1)}>
                  <label className="plain">{t.tests.structure.from}
                    <input type="number" min={1} max={200} value={r.from} onChange={(e) => patchRange(s.uid, r.uid, { from: e.target.value })} />
                  </label>
                  <label className="plain">{t.tests.structure.to}
                    <input type="number" min={1} max={200} value={r.to} onChange={(e) => patchRange(s.uid, r.uid, { to: e.target.value })} />
                  </label>
                  <label className="plain">{t.tests.structure.kind}
                    <select value={r.questionType} onChange={(e) => patchRange(s.uid, r.uid, { questionType: e.target.value as QuestionType, wordLimit: GROUP_OF[e.target.value as QuestionType] === 'completion' ? r.wordLimit : '' })}>
                      {QUESTION_TYPES.map((kind) => <option key={kind} value={kind}>{t.tests.questionTypes[kind]}</option>)}
                    </select>
                  </label>
                  {GROUP_OF[r.questionType] === 'completion' && (
                    <label className="plain">{t.tests.structure.wordLimit}
                      <input type="number" min={1} max={20} value={r.wordLimit} onChange={(e) => patchRange(s.uid, r.uid, { wordLimit: e.target.value })} />
                    </label>
                  )}
                  <button type="button" className="danger ghost" onClick={() => patch(s.uid, { ranges: s.ranges.filter((x) => x.uid !== r.uid) })}>{t.tests.structure.removeRange}</button>
                </div>
              ))}
              <div className="row-actions">
                <button type="button" className="secondary" onClick={() => addRange(s)}><Icon name="plus" /><span>{t.tests.structure.addRange}</span></button>
                <button type="button" className="danger ghost" onClick={() => edit((list) => list.filter((x) => x.uid !== s.uid))}>{t.tests.structure.removeSection}</button>
              </div>
            </fieldset>
          ))}
          <button type="button" className="secondary" onClick={addSection}><Icon name="plus" /><span>{t.tests.structure.addSection}</span></button>
        </fieldset>
        <p className="total"><strong>{t.tests.structure.total(drafts.length, total)}</strong></p>
        <p className="muted">{(Object.keys(counts) as QuestionGroup[]).map((g) => `${t.tests.structure.groups[g]} ${counts[g]}`).join(' · ')}</p>
        {problem && <p className="alert" role="alert"><span>{problem === 'pages' ? t.tests.structure.badPages : t.tests.structure.badRange}</span></p>}
        <ErrorAlert error={save.error} />
        {save.data === true && !dirty && <Notice>{t.tests.structure.saved}</Notice>}
        {locked && <p className="muted">{t.tests.locked}</p>}
        <div className="row-actions end">
          <button type="button" className="secondary" disabled={locked || save.isPending || !dirty} onClick={() => save.mutate()}>{t.tests.structure.save}</button>
          <button type="button" disabled={save.isPending} onClick={goOn}><span>{t.tests.next}</span><Icon name="arrow" /></button>
        </div>
      </section>
    </div>
  );
}
