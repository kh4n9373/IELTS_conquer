import { Fragment, type ReactNode } from 'react';
import type { ContentGroup, ContentOption, ContentSection } from '../lib/tests-api';

const BLANK = /\{\{\s*(\d{1,3})\s*\}\}/g;

/** Plain text with {{n}} turned into a numbered blank. Text is never parsed as HTML. */
function withBlanks(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(BLANK)) {
    out.push(text.slice(last, m.index));
    out.push(
      <span key={m.index} className="xc-blank">
        <b>{m[1]}</b>
        <span aria-hidden="true">__________</span>
      </span>,
    );
    last = (m.index ?? 0) + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

function Options({ options }: { options: ContentOption[] }) {
  return (
    <ul className="xc-options">
      {options.map((o) => (
        <li key={o.key}>
          <b>{o.key}</b> <span>{o.text}</span>
        </li>
      ))}
    </ul>
  );
}

function GroupContent({ group }: { group: ContentGroup }) {
  if (!group.content) return null;
  const lines = group.content.split('\n');
  if (group.type === 'table_completion' && lines.every((l) => l.includes('|') || !l.trim())) {
    const rows = lines.filter((l) => l.trim()).map((l) => l.split('|').map((c) => c.trim()));
    const [head = [], ...body] = rows;
    return (
      <div className="xc-table-wrap">
        <table className="xc-table">
          <thead>
            <tr>{head.map((c, i) => <th key={i}>{withBlanks(c)}</th>)}</tr>
          </thead>
          <tbody>
            {body.map((r, i) => (
              <tr key={i}>{r.map((c, j) => <td key={j}>{withBlanks(c)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return <p className="xc-text">{withBlanks(group.content)}</p>;
}

function Group({ group }: { group: ContentGroup }) {
  const numbers = group.questions.map((q) => q.number);
  const range = numbers.length > 1 ? `${numbers[0]}–${numbers[numbers.length - 1]}` : String(numbers[0]);
  return (
    <section className="xc-group">
      <h3>Questions {range}</h3>
      <p className="xc-instructions">{group.instructions}</p>
      {group.prompt && <p className="xc-prompt">{group.prompt}</p>}
      {group.options.length > 0 && <Options options={group.options} />}
      <GroupContent group={group} />
      {group.select_count === 1 && (
        <ol className="xc-questions">
          {group.questions
            .filter((q) => q.prompt || q.options)
            .map((q) => (
              <li key={q.number} value={q.number}>
                <span className="xc-no">{q.number}</span>
                <div>
                  {q.prompt && <p>{withBlanks(q.prompt)}</p>}
                  {q.options && <Options options={q.options} />}
                </div>
              </li>
            ))}
        </ol>
      )}
    </section>
  );
}

/** One passage and its questions, as the student reads them. */
export function ExamContentView({ section }: { section: ContentSection | undefined }) {
  if (!section) return null;
  return (
    <article className="xc">
      <div className="xc-passage">
        <p className="xc-eyebrow">{section.title}</p>
        {section.passage.title && <h2>{section.passage.title}</h2>}
        {section.passage.paragraphs.map((p, i) => (
          <p key={i} className="xc-para">
            {p.label && <b className="xc-label">{p.label}</b>}
            {p.text}
          </p>
        ))}
      </div>
      <div className="xc-groups">
        {section.groups.map((g, i) => (
          <Fragment key={i}>
            <Group group={g} />
          </Fragment>
        ))}
      </div>
    </article>
  );
}
