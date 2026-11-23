import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { ErrorAlert, Loading, PageHead } from '../../components/ui';
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

function Released({ data }: { data: ResultReleased | ResultPreview }) {
  const { t } = useI18n();
  const [onlyWrong, setOnlyWrong] = useState(false);
  const rows = useMemo(() => data.items.filter((i) => !onlyWrong || !i.correct), [data.items, onlyWrong]);
  const hasBand = data.band !== undefined;
  const note = !data.released
    ? t.result.teacherView
    : !data.countsForProgress
      ? data.attemptNumber > 1 ? t.result.retake : t.result.trial
      : null;
  return (
    <>
      <PageHead title={t.result.title} lede={t.result.attempt(data.attemptNumber)} />
      <div className={`res-stats${hasBand ? ' has-band' : ''}`}>
        <div className="res-stat"><strong>{`${data.correct}/${data.total}`}</strong><span>{t.result.correct}</span></div>
        <div className="res-stat"><strong>{`${Math.round(data.percent)}%`}</strong><span>{t.result.rate}</span></div>
        {hasBand && (
          <div className="res-stat is-band">
            <strong>{data.band}</strong>
            <span>{t.result.band(data.bandTableVersion ?? '')}</span>
          </div>
        )}
      </div>
      <p className="res-note">
        {data.sections.map((s) => `${s.title}: ${s.correct}/${s.total}`).join(' · ')}
      </p>
      {!hasBand && <p className="res-note">{t.result.noBand}</p>}
      {note && <p className="res-note">{note}</p>}
      <section className="res-detail">
        <div className="res-detail-head">
          <h2>{t.result.detail}</h2>
          <button type="button" className="auto" aria-pressed={onlyWrong} onClick={() => setOnlyWrong(!onlyWrong)}>
            {onlyWrong ? t.result.showAll : t.result.onlyWrong}
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th scope="col">{t.result.question}</th>
              <th scope="col">{t.result.yours}</th>
              <th scope="col">{t.result.mark}</th>
              <th scope="col">{t.result.key}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => (
              <tr key={i.number}>
                <th scope="row">{i.number}</th>
                <td>{i.answer ? i.answer : t.result.blank}</td>
                <td className={i.correct ? 'is-right' : 'is-wrong'}>{i.correct ? t.result.right : t.result.wrong}</td>
                <td>{i.key.join(' / ')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <Link className="button-link auto" to="/assignments" viewTransition>{t.result.back}</Link>
    </>
  );
}
