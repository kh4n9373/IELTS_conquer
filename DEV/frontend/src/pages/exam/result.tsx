import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { ErrorAlert, Icon, Loading, PageHead } from '../../components/ui';
import { examApi, type ResultPreview, type ResultReleased } from '../../lib/exam-api';
import { useI18n } from '../../lib/i18n';
import { useDateTime } from '../assignments/list';
import '../../styles/result.css';

export function ResultPage() {
  const { attemptId = '' } = useParams();
  const { t } = useI18n();
  const dateTime = useDateTime();
  const result = useQuery({ queryKey: ['result', attemptId], queryFn: () => examApi.result(attemptId) });

  if (result.isPending) return <Loading />;
  if (result.isError) {
    return (
      <>
        <PageHead title={t.result.title} />
        <ErrorAlert error={result.error} />
        <button type="button" className="auto" onClick={() => void result.refetch()}>{t.result.retry}</button>
      </>
    );
  }
  const data = result.data;
  if (!('correct' in data)) {
    return (
      <>
        <PageHead title={t.result.title} />
        <section className="res-pending">
          <h2>{t.result.submittedTitle}</h2>
          <p>{data.submittedAt ? t.result.submittedLine(data.testTitle, dateTime(data.submittedAt)) : data.testTitle}</p>
          <div className="res-expect">
            <strong>{t.result.expected(dateTime(data.expectedAt))}</strong>
            <span>{t.result.releaseNote}</span>
          </div>
          <Link className="button-link auto" to="/assignments" viewTransition>{t.result.back}</Link>
        </section>
      </>
    );
  }
  return <Released data={data} />;
}

type Item = ResultReleased['items'][number];
type Filter = 'all' | 'wrong' | 'blank';
const statusOf = (i: Item) => (i.correct ? 'right' : i.answer ? 'wrong' : 'blank');
const PAGE = 10;

function Released({ data }: { data: ResultReleased | ResultPreview }) {
  const { t } = useI18n();
  const { attemptId = '' } = useParams();
  const dateTime = useDateTime();
  const [filter, setFilter] = useState<Filter>('all');
  const [page, setPage] = useState(0);
  const counts = useMemo(() => {
    const c = { right: 0, wrong: 0, blank: 0 };
    for (const i of data.items) c[statusOf(i)] += 1;
    return c;
  }, [data.items]);
  const rows = useMemo(
    () => data.items.filter((i) => filter === 'all' || (filter === 'wrong' ? !i.correct : statusOf(i) === 'blank')),
    [data.items, filter],
  );
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE);
  const pick = (f: Filter) => {
    setFilter(f);
    setPage(0);
  };
  const hasBand = data.band !== undefined;
  const percent = Math.round(data.percent);
  const notes = [
    !data.released ? t.result.teacherView : data.isTrial ? t.result.trial : data.attemptNumber > 1 ? t.result.retake : null,
    hasBand ? null : t.result.noBand,
  ].filter(Boolean);
  const filters: [Filter, string, number][] = [
    ['all', t.result.filterAll, data.total],
    ['wrong', t.result.onlyWrong, counts.wrong + counts.blank],
    ['blank', t.result.filterBlank, counts.blank],
  ];
  const jump = (n: number) => {
    setFilter('all');
    setPage(Math.floor((data.items.findIndex((i) => i.number === n)) / PAGE));
    requestAnimationFrame(() => {
      const row = document.getElementById(`res-q-${n}`);
      row?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      row?.focus({ preventScroll: true });
    });
  };
  return (
    <div className="res">
      <header className="res-hero in">
        <div className="res-hero-copy">
          <p className="eyebrow">{t.result.title}</p>
          <h1>{data.testTitle}</h1>
          <ul className="res-meta">
            <li>{t.result.attempt(data.attemptNumber, data.maxAttempts)}</li>
            <li>{t.result.submittedAt(dateTime(data.submittedAt))}</li>
            {data.isTrial && <li className="is-tag">{t.result.trialLabel}</li>}
          </ul>
        </div>
        <div className="res-score">
          {hasBand ? (
            <>
              <span className="res-score-label">{t.result.band(data.bandTableVersion ?? '')}</span>
              <strong className="res-score-big">{data.band}</strong>
              <span className="res-score-sub"><b>{`${data.correct}/${data.total}`}</b> {t.result.correct.toLowerCase()}</span>
            </>
          ) : (
            <>
              <span className="res-score-label">{t.result.correct}</span>
              <strong className="res-score-big">{`${data.correct}/${data.total}`}</strong>
            </>
          )}
          <div className="res-meter" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} aria-label={t.result.rate}>
            <span style={{ width: `${percent}%` }} />
          </div>
          <span className="res-percent"><b>{`${percent}%`}</b> {t.result.rate.toLowerCase()}</span>
        </div>
        <div className="res-split">
          <div className="res-split-bar" role="img"
            aria-label={`${t.result.right} ${counts.right}, ${t.result.wrong} ${counts.wrong}, ${t.result.blankLabel} ${counts.blank}`}>
            {(['right', 'wrong', 'blank'] as const).map((k) => counts[k] > 0 && <span key={k} className={`is-${k}`} style={{ flexGrow: counts[k] }} />)}
          </div>
          <dl className="res-split-legend">
            <div className="is-right"><dt>{t.result.right}</dt><dd>{counts.right}</dd></div>
            <div className="is-wrong"><dt>{t.result.wrong}</dt><dd>{counts.wrong}</dd></div>
            <div className="is-blank"><dt>{t.result.blankLabel}</dt><dd>{counts.blank}</dd></div>
          </dl>
        </div>
      </header>

      {notes.length > 0 && (
        <ul className="res-notes">
          {notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}

      <div className="res-grid">
        <section className="res-card res-sections" aria-labelledby="res-sections-h">
          <h2 id="res-sections-h">{t.result.bySection}</h2>
          <ul>
            {data.sections.map((s) => {
              const p = s.total ? Math.round((s.correct / s.total) * 100) : 0;
              return (
                <li key={s.position}>
                  <div className="res-sec-line">
                    <span className="res-sec-title">{s.title}</span>
                    <span className="res-sec-score">{`${s.correct}/${s.total}`}</span>
                  </div>
                  <div className="res-bar"><span style={{ width: `${p}%` }} /></div>
                </li>
              );
            })}
          </ul>
        </section>

        <section className="res-card res-map" aria-labelledby="res-map-h">
          <h2 id="res-map-h">{t.result.map}</h2>
          <ol>
            {data.items.map((i) => {
              const s = statusOf(i);
              return (
                <li key={i.number}>
                  <button type="button" className={`res-cell is-${s}`} onClick={() => jump(i.number)}
                    aria-label={`${t.result.question} ${i.number}: ${s === 'right' ? t.result.right : s === 'wrong' ? t.result.wrong : t.result.blankLabel}`}>
                    {i.number}
                  </button>
                </li>
              );
            })}
          </ol>
          <p className="res-legend">
            <span><i className="is-right" />{t.result.right}</span>
            <span><i className="is-wrong" />{t.result.wrong}</span>
            <span><i className="is-blank" />{t.result.blankLabel}</span>
          </p>
        </section>
      </div>

      <section className="res-card res-detail" aria-labelledby="res-detail-h">
        <div className="res-detail-head">
          <h2 id="res-detail-h">{t.result.detail}</h2>
          <div className="res-filters" role="group" aria-label={t.result.detail}>
            {filters.map(([key, label, n]) => (
              <button key={key} type="button" aria-pressed={filter === key} onClick={() => pick(key)}>
                {label}<span className="res-count">{n}</span>
              </button>
            ))}
          </div>
        </div>
        {rows.length === 0 ? (
          <p className="res-empty">{t.result.noneHere}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">{t.result.question}</th>
                <th scope="col">{t.result.yours}</th>
                <th scope="col">{t.result.key}</th>
                <th scope="col">{t.result.mark}</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((i) => {
                const s = statusOf(i);
                return (
                  <tr key={i.number} id={`res-q-${i.number}`} tabIndex={-1} className={`is-${s}`}>
                    <th scope="row">{i.number}</th>
                    <td className="res-answer">{i.answer ? i.answer : <span className="res-blank">{t.result.blank}</span>}</td>
                    <td className="res-key">{i.key.join(' / ')}</td>
                    <td><span className={`res-badge is-${s}`}>{i.correct ? t.result.right : t.result.wrong}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {pages > 1 && (
          <nav className="res-pager" aria-label={t.result.pages}>
            <button type="button" className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label={t.result.prevPage}>‹</button>
            {Array.from({ length: pages }, (_, k) => (
              <button key={k} type="button" aria-current={k === page ? 'page' : undefined} onClick={() => setPage(k)}
                aria-label={t.result.pageOf(k * PAGE + 1, Math.min((k + 1) * PAGE, rows.length))}>
                {k + 1}
              </button>
            ))}
            <button type="button" className="secondary" disabled={page === pages - 1} onClick={() => setPage(page + 1)} aria-label={t.result.nextPage}>›</button>
          </nav>
        )}
      </section>
      <div className="res-actions">
        <Link className="button-link auto secondary" to="/assignments" viewTransition><Icon name="back" />{t.result.back}</Link>
        {data.released && (
          <Link className="button-link auto" to={`/results/${attemptId}/review`} viewTransition>{t.review.details}<Icon name="arrow" /></Link>
        )}
      </div>
    </div>
  );
}
