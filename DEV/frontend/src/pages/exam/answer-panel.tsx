import { useI18n } from '../../lib/i18n';
import type { ExamRoom } from '../../lib/exam-api';

export type LocalAnswer = { value: string; marked: boolean; seq: number };
export type Answers = Record<number, LocalAnswer>;

export const isAnswered = (a: LocalAnswer | undefined) => !!a && a.value.trim() !== '';

type Props = {
  questions: ExamRoom['questions'];
  answers: Answers;
  current: number;
  locked: boolean;
  onPick: (number: number) => void;
  onEdit: (number: number, value: string) => void;
  onMark: (number: number) => void;
};

/** The 40-question grid and one answer row per question. Typing is local state: nothing refetched replaces it. */
export function AnswerPanel({ questions, answers, current, locked, onPick, onEdit, onMark }: Props) {
  const { t } = useI18n();
  return (
    <aside className="exam-sheet" aria-label={t.exam.sheet}>
      <h2>{t.exam.sheet}</h2>
      <div className="exam-grid">
        {questions.map(({ number }) => {
          const answered = isAnswered(answers[number]);
          const marked = answers[number]?.marked ?? false;
          return (
            <button
              key={number}
              type="button"
              className={`xb exam-cell${answered ? ' is-done' : ''}${marked ? ' is-marked' : ''}${number === current ? ' is-current' : ''}`}
              aria-label={t.exam.cell(number, answered, marked)}
              onClick={() => onPick(number)}
            >
              {number}
            </button>
          );
        })}
      </div>
      <div className="exam-legend">
        <span><b>■</b> {t.exam.answered}</span>
        <span>□ {t.exam.blank}</span>
        <span className="mark">{t.exam.review}</span>
      </div>
      <div className="exam-rows">
        {questions.map(({ number, wordLimit }) => {
          const a = answers[number];
          return (
            <div className="exam-row" key={number}>
              <label htmlFor={`exam-q${number}`}>{`${t.exam.q} ${number}`}</label>
              <input
                id={`exam-q${number}`}
                type="text"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                maxLength={200}
                value={a?.value ?? ''}
                disabled={locked}
                placeholder={wordLimit ? `≤ ${wordLimit}` : undefined}
                className={number === current ? 'is-current' : undefined}
                onFocus={() => onPick(number)}
                onChange={(e) => onEdit(number, e.target.value)}
              />
              <button
                type="button"
                className={`xb exam-flag${a?.marked ? ' is-on' : ''}`}
                aria-pressed={a?.marked ?? false}
                aria-label={`${t.exam.review} ${t.exam.q} ${number}`}
                disabled={locked}
                onClick={() => onMark(number)}
              >
                {t.exam.review}
              </button>
            </div>
          );
        })}
      </div>
    </aside>
  );
}
