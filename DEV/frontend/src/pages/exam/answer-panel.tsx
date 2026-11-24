import { useI18n } from '../../lib/i18n';
import type { ExamRoom } from '../../lib/exam-api';

export type LocalAnswer = { value: string; marked: boolean; seq: number };
export type Answers = Record<number, LocalAnswer>;

export const isAnswered = (a: LocalAnswer | undefined) => !!a && a.value.trim() !== '';

type Props = {
  /** Only the current passage's questions. Answers for the others stay in `answers`. */
  questions: ExamRoom['questions'];
  answers: Answers;
  current: number;
  /** On a phone only the current question is shown; the bottom bar moves between them. */
  phone: boolean;
  locked: boolean;
  /** Focus inside a question makes it the current one, without moving focus again. */
  onCurrent: (number: number) => void;
  onEdit: (number: number, value: string) => void;
  onMark: (number: number) => void;
};

export function FlagIcon() {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 21V4h11l-2 4 2 4H5" />
    </svg>
  );
}

/** Typed tests get one card per question; PDF tests get one compact line per answer box. */
export function AnswerPanel({ questions, answers, current, phone, locked, onCurrent, onEdit, onMark }: Props) {
  const { t } = useI18n();
  const first = questions[0]?.number;
  const last = questions[questions.length - 1]?.number;
  const typed = questions.some((q) => q.prompt !== '' || q.options.length > 0);
  const shown = questions.filter((q) => !phone || q.number === current);

  const flag = (number: number, compact: boolean) => {
    const on = answers[number]?.marked ?? false;
    const label = phone ? (on ? t.exam.unmarkReview : t.exam.markReview) : on ? t.exam.unmarkQ(number) : t.exam.markQ(number);
    return (
      <button type="button" className={`exam-flag${on ? ' is-on' : ''}${compact ? ' is-compact' : ''}`} aria-pressed={on} aria-label={label} disabled={locked} onClick={() => onMark(number)}>
        <FlagIcon />
        {!compact && <span>{t.exam.review}</span>}
      </button>
    );
  };

  return (
    <section className={`exam-sheet${typed ? ' is-cards' : ' is-lines'}`} aria-label={t.exam.sheet}>
      {first !== undefined && last !== undefined && <h2 className="exam-range">{t.exam.range(first, last)}</h2>}
      {shown.map(({ number, wordLimit, type, prompt, options }) => {
        const a = answers[number];
        const cls = `${isAnswered(a) ? ' is-done' : ''}${number === current ? ' is-current' : ''}`;
        if (!typed)
          return (
            <div key={number} className={`exam-line${cls}`}>
              <span className="exam-num" aria-hidden="true">{number}</span>
              <input
                id={`exam-q${number}`}
                aria-label={`${t.exam.q} ${number}`}
                type="text"
                autoComplete="off"
                autoCapitalize="off"
                spellCheck={false}
                maxLength={200}
                value={a?.value ?? ''}
                disabled={locked}
                placeholder={wordLimit ? `≤ ${wordLimit}` : undefined}
                onFocus={() => onCurrent(number)}
                onChange={(e) => onEdit(number, e.target.value)}
              />
              {flag(number, !phone)}
            </div>
          );
        return (
          <div key={number} className={`exam-card${cls}`} onFocus={() => onCurrent(number)}>
            <div className="exam-card-head">
              <span className="exam-num" aria-hidden="true">{number}</span>
              <p className="exam-prompt">{prompt}</p>
              {flag(number, false)}
            </div>
            <TypedInput number={number} type={type} options={options} value={a?.value ?? ''} locked={locked} onEdit={onEdit} />
          </div>
        );
      })}
    </section>
  );
}

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

type TypedProps = {
  number: number;
  type: ExamRoom['questions'][number]['type'];
  options: string[];
  value: string;
  locked: boolean;
  onEdit: (number: number, value: string) => void;
};

/** Typed tests: radios, checkboxes (value "A, C"), a word-bank list, or a text box. */
function TypedInput({ number, type, options, value, locked, onEdit }: TypedProps) {
  const { t } = useI18n();
  const id = `exam-q${number}`;
  const name = `${t.exam.q} ${number}`;
  if (type === 'multiple_choice' || type === 'multiple_choice_multi') {
    const multi = type === 'multiple_choice_multi';
    const picked = new Set(value.toUpperCase().match(/[A-Z]/g) ?? []);
    const toggle = (letter: string) => {
      if (!multi) return onEdit(number, letter);
      if (picked.has(letter)) picked.delete(letter);
      else picked.add(letter);
      onEdit(number, [...picked].sort().join(', '));
    };
    return (
      <div role={multi ? 'group' : 'radiogroup'} aria-label={name} className="exam-choices">
        {options.map((option, i) => {
          const letter = LETTERS[i] as string;
          return (
            <label key={letter} className="exam-opt">
              <input id={i === 0 ? id : undefined} type={multi ? 'checkbox' : 'radio'} name={id} checked={picked.has(letter)} disabled={locked} onChange={() => toggle(letter)} />
              <b>{letter}</b>
              <span>{option}</span>
            </label>
          );
        })}
      </div>
    );
  }
  if (options.length > 0)
    return (
      <select id={id} aria-label={name} className="exam-field" value={value} disabled={locked} onChange={(e) => onEdit(number, e.target.value)}>
        <option value="">{t.exam.choose}</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  return (
    <input id={id} aria-label={name} className="exam-field" type="text" autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={200} value={value} disabled={locked}
      onChange={(e) => onEdit(number, e.target.value)} />
  );
}
