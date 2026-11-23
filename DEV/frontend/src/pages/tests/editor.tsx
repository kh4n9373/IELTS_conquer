import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams } from 'react-router';
import { pdfUrlKey } from '../../components/pdf-viewer';
import { ErrorAlert, Field, Icon, Loading, Notice, order, PageHead } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { testsApi, type CreateTest, type SourceType, type TestDetail, type TestType } from '../../lib/tests-api';

export type EditorContext = { test: TestDetail };
export const useTest = () => useOutletContext<EditorContext>();

const STEPS = [
  ['', 'info'],
  ['structure', 'structure'],
  ['key', 'key'],
  ['publish', 'publish'],
] as const;
const SOURCES: SourceType[] = ['self_authored', 'licensed', 'commercial', 'other'];
const TYPES: TestType[] = ['course', 'ielts_standard'];
const DEFAULT_MINUTES: Record<TestType, number> = { course: 120, ielts_standard: 60 };

export const detailKey = (id: string) => ['test', id] as const;

/** Loads one test for every step, and polls while OCR is still running. */
export function EditorLayout() {
  const { t } = useI18n();
  const id = useParams().id ?? '';
  const { pathname } = useLocation();
  const test = useQuery({
    queryKey: detailKey(id),
    queryFn: () => testsApi.get(id),
    refetchInterval: (q) => (['queued', 'running'].includes(q.state.data?.ocr?.state ?? '') ? 5000 : false),
  });

  if (test.isPending) return <Loading />;
  if (test.isError) return <ErrorAlert error={test.error} />;
  const data = test.data;
  const step = pathname.split('/')[3] ?? '';
  const known = STEPS.find(([path]) => path === step)?.[1];
  const heading = t.tests.titles[known ?? (step === 'preview' ? 'preview' : 'info')];

  return (
    <>
      <Link className="back-link in" style={order(0)} to="/tests" viewTransition>
        <Icon name="back" />
        <span>{t.tests.all}</span>
      </Link>
      <PageHead eyebrow={`${data.title} · ${t.tests.status[data.status]}`} title={heading}>
        <Link className="button-link auto" to={`/tests/${id}/preview`} viewTransition>
          <span>{t.tests.preview}</span>
        </Link>
      </PageHead>
      <nav className="steps in" style={order(3)} aria-label={t.tests.steps.label}>
        {STEPS.map(([path, name]) => (
          <NavLink key={name} to={path ? `/tests/${id}/${path}` : `/tests/${id}`} end replace>
            {t.tests.steps[name]}
          </NavLink>
        ))}
      </nav>
      <div className="in" style={order(4)}>
        <Outlet context={{ test: data } satisfies EditorContext} />
      </div>
    </>
  );
}

function DetailsForm({ test }: { test?: TestDetail }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const navigate = useNavigate();
  const locked = test !== undefined && test.status !== 'draft';
  const [type, setType] = useState<TestType>(test?.testType ?? 'course');
  const [minutes, setMinutes] = useState(String(test?.durationMinutes ?? DEFAULT_MINUTES.course));
  const [file, setFile] = useState<File | null>(null);
  // A test made by a failed submit is reused by the retry, so a retry never makes a second test.
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [uploadFailed, setUploadFailed] = useState(false);

  const pickType = (next: TestType) => {
    setType(next);
    if (minutes === String(DEFAULT_MINUTES[type])) setMinutes(String(DEFAULT_MINUTES[next]));
  };

  const save = useMutation({
    mutationFn: async (values: CreateTest) => {
      setUploadFailed(false);
      let id = test?.id ?? createdId;
      if (id === null) {
        id = (await testsApi.create(values)).id;
        setCreatedId(id);
        client.invalidateQueries({ queryKey: ['tests'] });
      } else if (!locked) {
        await testsApi.update(id, values);
      }
      if (file) {
        try {
          await testsApi.uploadPdf(id, file);
        } catch (err) {
          setUploadFailed(true);
          throw err;
        }
        setFile(null);
        client.invalidateQueries({ queryKey: pdfUrlKey(id) });
      }
      return id;
    },
    onSuccess: (id) => {
      client.invalidateQueries({ queryKey: detailKey(id) });
      client.invalidateQueries({ queryKey: ['tests'] });
      if (test === undefined) navigate(`/tests/${id}/structure`);
    },
  });

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    save.mutate({
      title: String(form.get('title') ?? '').trim(),
      sourceType: String(form.get('sourceType')) as SourceType,
      testType: type,
      durationMinutes: Number(minutes),
    });
  };
  const pdf = test?.studentPdf ?? null;

  return (
    <form className="details" onSubmit={submit}>
      <div className="stack">
        <Field label={t.tests.form.title} name="title" type="text" maxLength={120} required defaultValue={test?.title} disabled={locked} />
        <fieldset disabled={locked}>
          <legend>{t.tests.form.type}</legend>
          {TYPES.map((kind) => (
            <button key={kind} type="button" className={`type-option${type === kind ? ' on' : ''}`} aria-pressed={type === kind} onClick={() => pickType(kind)}>
              <strong>{kind === 'course' ? t.tests.form.typeCourse : t.tests.form.typeIelts}</strong>
              <span>{kind === 'course' ? t.tests.form.typeCourseBody : t.tests.form.typeIeltsBody}</span>
            </button>
          ))}
        </fieldset>
        <div className="pair">
          <div className="field">
            <label htmlFor="source">{t.tests.form.source}</label>
            <select id="source" name="sourceType" defaultValue={test?.sourceType ?? 'self_authored'} disabled={locked}>
              {SOURCES.map((s) => <option key={s} value={s}>{t.tests.sources[s]}</option>)}
            </select>
          </div>
          <Field label={t.tests.form.minutes} name="minutes" type="number" min={1} max={300} required value={minutes} onChange={(e) => setMinutes(e.target.value)} disabled={locked} />
        </div>
      </div>
      <div className="stack">
        <div className="field">
          <label htmlFor="pdf">{t.tests.form.pdf}</label>
          <div className={`drop${uploadFailed ? ' is-bad' : ''}`}>
            <strong>{pdf ? t.tests.form.pdfDone(pdf.pageCount) : t.tests.form.pdfNone}</strong>
            <input id="pdf" type="file" accept="application/pdf" required={!pdf && createdId === null} disabled={locked} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            {pdf && <span className="muted">{t.tests.form.pdfChange}</span>}
          </div>
        </div>
        <ErrorAlert error={save.error} />
        {uploadFailed && <p className="muted">{t.tests.form.uploadHelp}</p>}
        {save.isSuccess && test !== undefined && <Notice>{t.tests.form.saved}</Notice>}
        <p className="muted">{t.tests.form.ocrNotice}</p>
        {locked && <p className="muted">{t.tests.locked}</p>}
        <button className="primary-wide" disabled={save.isPending || locked}>
          <span>{save.isPending ? t.tests.form.working : test === undefined ? t.tests.next : t.tests.form.save}</span>
          <Icon name="arrow" />
        </button>
      </div>
    </form>
  );
}

export function NewTestPage() {
  const { t } = useI18n();
  return (
    <>
      <Link className="back-link in" style={order(0)} to="/tests" viewTransition>
        <Icon name="back" />
        <span>{t.tests.all}</span>
      </Link>
      <PageHead title={t.tests.titles.create} />
      <div className="in" style={order(3)}><DetailsForm /></div>
    </>
  );
}

export function DetailsPage() {
  return <DetailsForm test={useTest().test} />;
}
