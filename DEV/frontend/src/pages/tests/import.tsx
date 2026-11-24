import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { ErrorAlert, Icon, Notice, useConfirm } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { testsApi, type ImportIssue, type ImportReport } from '../../lib/tests-api';

const GUIDE_URL = '/api/exam-schema/v1/ai-guide.md';

async function guideText(): Promise<string> {
  const res = await fetch(GUIDE_URL, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('guide');
  return res.text();
}

function issueLine(i: ImportIssue): string {
  return `[${i.code}] ${i.path}${i.number ? ` (câu ${i.number})` : ''}: ${i.message}`;
}

/** Paste AI output, check it, then create a test (or replace a draft's content when `testId` is given). */
export function ImportPanel({ testId, onDone }: { testId?: string; onDone?: () => void }) {
  const { t } = useI18n();
  const c = t.tests.importAi;
  const client = useQueryClient();
  const navigate = useNavigate();
  const { ask, dialog } = useConfirm();
  const [text, setText] = useState('');
  const [report, setReport] = useState<ImportReport | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const check = useMutation({ mutationFn: () => testsApi.validateImport(text), onSuccess: setReport });
  const create = useMutation({
    mutationFn: async () => {
      if (testId) {
        client.setQueryData(['test', testId], await testsApi.reimport(testId, text));
        return testId;
      }
      return (await testsApi.createImport(text)).id;
    },
    onSuccess: (id) => {
      client.invalidateQueries({ queryKey: ['tests'] });
      client.invalidateQueries({ queryKey: ['test', id] });
      if (onDone) onDone();
      else navigate(`/tests/${id}/structure`);
    },
  });

  const copy = async (what: 'guide' | 'errors') => {
    const value = what === 'guide' ? await guideText() : (report?.errors ?? []).map(issueLine).join('\n');
    await navigator.clipboard.writeText(what === 'errors' ? `${c.fixPrompt}\n\n${value}` : value);
    setCopied(what);
  };
  const download = async () => {
    const url = URL.createObjectURL(new Blob([await guideText()], { type: 'text/markdown' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: 'panxg-exam-v1-ai-guide.md' });
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="import-page">
      {dialog}
      {!testId && (
        <div className="import-steps">
          <div>
            <strong>{c.step1}</strong>
            <span className="muted">{c.step1Body}</span>
            <span className="import-actions">
              <button type="button" className="secondary" onClick={() => void download()}>{c.download}</button>
              <button type="button" className="secondary" onClick={() => void copy('guide')}>{copied === 'guide' ? c.copied : c.copyGuide}</button>
            </span>
          </div>
          <div>
            <strong>{c.step2}</strong>
            <span className="muted">{c.step2Body}</span>
            <code>{c.prompt}</code>
          </div>
          <div>
            <strong>{c.step3}</strong>
            <span className="muted">{c.step3Body}</span>
          </div>
        </div>
      )}
      <div className="field">
        <label htmlFor="import-json">{c.paste}</label>
        <textarea
          id="import-json"
          value={text}
          spellCheck={false}
          placeholder='```json { "schema": "panxg-exam/v1", ... }'
          onChange={(e) => {
            setText(e.target.value);
            setReport(null);
          }}
        />
      </div>
      <ErrorAlert error={check.error ?? create.error} />
      {report && !report.ok && (
        <section>
          <p role="alert"><strong>{c.errors(report.errors.length)}</strong></p>
          <ul className="import-issues">
            {report.errors.map((i, k) => <li key={k} className="is-error">{issueLine(i)}</li>)}
          </ul>
          <p className="muted">{c.errorsHelp}</p>
          <button type="button" className="secondary" onClick={() => void copy('errors')}>{copied === 'errors' ? c.copied : c.copyErrors}</button>
        </section>
      )}
      {report?.ok && report.summary && (
        <section>
          <Notice>{c.summary(report.summary.title, report.summary.sections, report.summary.questions)}</Notice>
          <ul className="import-issues">
            {Object.entries(report.summary.byType).map(([type, n]) => (
              <li key={type}>{t.tests.questionTypes[type as keyof typeof t.tests.questionTypes] ?? type}: {n}</li>
            ))}
          </ul>
          {report.warnings.length > 0 && (
            <>
              <p><strong>{c.warnings(report.warnings.length)}</strong></p>
              <ul className="import-issues">
                {report.warnings.map((i, k) => <li key={k}>{issueLine(i)}</li>)}
              </ul>
            </>
          )}
          <p className="muted">{c.reviewNote}</p>
        </section>
      )}
      <span className="import-actions">
        <button type="button" className="secondary" disabled={!text.trim() || check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? t.tests.form.working : c.check}
        </button>
        <button type="button" className="primary-wide" disabled={!report?.ok || create.isPending} onClick={() =>
            testId
              ? ask({ title: c.reimport, body: c.reimportConfirm, action: c.reimport, run: () => create.mutate() })
              : create.mutate()
          }>
          <span>{create.isPending ? t.tests.form.working : testId ? c.reimport : c.create}</span>
          <Icon name="arrow" />
        </button>
      </span>
    </div>
  );
}
