import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { ExamContentView } from '../../components/exam-content';
import { PdfViewer } from '../../components/pdf-viewer';
import { testsApi } from '../../lib/tests-api';
import { useI18n } from '../../lib/i18n';
import { useTest } from './editor';

/**
 * What a student would see: the PDF and an empty answer sheet.
 * It reads the test only. It never opens an attempt and never touches the answer routes.
 */
export function PreviewPage() {
  const { t } = useI18n();
  const { test } = useTest();
  const composed = test.authoringMode === 'composed';
  const content = useQuery({ queryKey: ['test-content', test.id], queryFn: () => testsApi.getContent(test.id), enabled: composed });
  const [passage, setPassage] = useState(0);
  const numbers = test.sections.flatMap((s) => s.questions.map((q) => q.number)).sort((a, b) => a - b);

  return (
    <div className="preview">
      <p role="status" className="banner">{t.tests.previewPage.banner}</p>
      <div className="workbench">
        {composed ? (
          <section className="exam-paper">
            <span className="exam-tabs">
              {test.sections.map((s, i) => (
                <button key={s.id} type="button" className={`xb${i === passage ? ' is-on' : ''}`} aria-pressed={i === passage} onClick={() => setPassage(i)}>
                  {s.title}
                </button>
              ))}
            </span>
            <ExamContentView section={content.data?.content.sections[passage]} />
          </section>
        ) : (
          <PdfViewer testId={test.id} hasPdf={test.studentPdf !== null} />
        )}
        <aside className="panel" aria-label={t.tests.previewPage.sheet}>
          <h2>{t.tests.previewPage.sheet}</h2>
          <ul className="sheet-list">
            {numbers.map((n) => (
              <li key={n}>
                <label>
                  <span>{t.tests.previewPage.question(n)}</span>
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
