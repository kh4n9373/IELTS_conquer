import { PdfViewer } from '../../components/pdf-viewer';
import { useI18n } from '../../lib/i18n';
import { useTest } from './editor';

/**
 * What a student would see: the PDF and an empty answer sheet.
 * It reads the test only. It never opens an attempt and never touches the answer routes.
 */
export function PreviewPage() {
  const { t } = useI18n();
  const { test } = useTest();
  const questions = test.sections.flatMap((s) => s.questions).sort((a, b) => a.number - b.number);

  return (
    <div className="preview">
      <p role="status" className="banner">{t.tests.previewPage.banner}</p>
      <div className="workbench">
        {test.authoring === 'manual' ? (
          <article className="panel preview-text">
            {test.sections.map((s) => (
              <section key={s.id}>
                <h2>{s.title}</h2>
                <p>{s.body}</p>
              </section>
            ))}
          </article>
        ) : (
          <PdfViewer testId={test.id} hasPdf={test.studentPdf !== null} />
        )}
        <aside className="panel" aria-label={t.tests.previewPage.sheet}>
          <h2>{t.tests.previewPage.sheet}</h2>
          <ul className="sheet-list">
            {questions.map((q) => (
              <li key={q.number}>
                <label>
                  <span>{t.tests.previewPage.question(q.number)}{q.prompt && ` · ${q.prompt}`}</span>
                  {q.options.length > 0 && <small>{q.options.map((o, i) => (q.questionType.startsWith('multiple_choice') ? `${String.fromCharCode(65 + i)}. ${o}` : o)).join('   ')}</small>}
                  <input type="text" disabled />
                </label>
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </div>
  );
}
