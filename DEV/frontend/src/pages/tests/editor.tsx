import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate, useOutletContext, useParams } from 'react-router';
import { ErrorAlert, Field, Icon, Loading, Notice, order, PageHead } from '../../components/ui';
import { useI18n } from '../../lib/i18n';
import { testsApi, type Authoring, type CreateTest, type SourceType, type TestDetail, type TestType } from '../../lib/tests-api';

export type EditorContext = { test: TestDetail };
export const useTest = () => useOutletContext<EditorContext>();

const STEPS = [
  ['', 'info'],
  ['structure', 'structure'],
  ['key', 'key'],
  ['publish', 'publish'],
] as const;
const SOURCES: SourceType[] = ['self_authored', 'licensed', 'commercial', 'other'];
const MODES: Authoring[] = ['pdf', 'manual'];
const DEFAULT_MINUTES: Record<TestType, number> = { course: 120, ielts_standard: 60 };

export const detailKey = (id: string) => ['test', id] as const;

/** Loads one test for every step, and polls while OCR is still running. */
/** Keeps the step area as tall as the step just left until the new step has loaded,
 * so a loading spinner does not shrink the page and throw the scroll back to the top. */
function useKeptHeight(step: string) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const last = useRef(0);
  useEffect(() => {
    const el = inner.current;
    if (!el) return;
    let timer = 0;
    const observer = new ResizeObserver(() => {
      last.current = el.offsetHeight;
      const box = outer.current;
      if (!box?.style.minHeight) return;
      clearTimeout(timer);
      // Released once the new step fills the space, or after it has held still for a moment.
      if (el.offsetHeight >= parseFloat(box.style.minHeight)) box.style.minHeight = '';
      else timer = window.setTimeout(() => { box.style.minHeight = ''; }, 1500);
    });
    observer.observe(el);
    return () => { observer.disconnect(); clearTimeout(timer); };
  });
  useLayoutEffect(() => {
    if (outer.current && last.current) outer.current.style.minHeight = `${last.current}px`;
  }, [step]);
  return { outer, inner };
}

export function EditorLayout() {
  const { t } = useI18n();
  const id = useParams().id ?? '';
  const { pathname } = useLocation();
  const kept = useKeptHeight(pathname);
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
          <NavLink key={name} to={path ? `/tests/${id}/${path}` : `/tests/${id}`} end preventScrollReset>
            {t.tests.steps[name]}
          </NavLink>
        ))}
      </nav>
      <div ref={kept.outer} className="in" style={order(4)}>
        <div ref={kept.inner}>
          <Outlet context={{ test: data } satisfies EditorContext} />
        </div>
      </div>
    </>
  );
}

function DetailsForm({ test }: { test?: TestDetail }) {
  const { t } = useI18n();
  const client = useQueryClient();
  const navigate = useNavigate();
  const locked = test !== undefined && test.status !== 'draft';
  const type: TestType = test?.testType ?? 'course';
  const [authoring, setAuthoring] = useState<Authoring>(test?.authoring ?? 'pdf');
  const [minutes, setMinutes] = useState(String(test?.durationMinutes ?? DEFAULT_MINUTES.course));
  // A test made by a failed submit is reused by the retry, so a retry never makes a second test.
  const [createdId, setCreatedId] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async (values: CreateTest) => {
      let id = test?.id ?? createdId;
      if (id === null) {
        id = (await testsApi.create({ ...values, authoring })).id;
        setCreatedId(id);
        client.invalidateQueries({ queryKey: ['tests'] });
      } else if (!locked) {
        await testsApi.update(id, values);
      }
      return id;
    },
    onSuccess: (id) => {
      client.invalidateQueries({ queryKey: detailKey(id) });
      client.invalidateQueries({ queryKey: ['tests'] });
      // Replaces the create form, so Back from the editor never lands on an empty form.
      if (test === undefined) navigate(`/tests/${id}/structure`, { replace: true });
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

  return (
    <form className="details" onSubmit={submit}>
      <div className="stack">
        <Field label={t.tests.form.title} name="title" type="text" maxLength={120} required defaultValue={test?.title} disabled={locked} />
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
        {/* The way a test is made is fixed once it exists: the next step depends on it. */}
        <fieldset disabled={test !== undefined}>
          <legend>{t.tests.form.mode}</legend>
          {MODES.filter((mode) => test === undefined || mode === authoring).map((mode) => (
            <button key={mode} type="button" className={`type-option mode-option${authoring === mode ? ' on' : ''}`} aria-pressed={authoring === mode} onClick={() => setAuthoring(mode)}>
              <strong>{t.tests.form.modes[mode]}</strong>
              <span>{t.tests.form.modesBody[mode]}</span>
            </button>
          ))}
        </fieldset>
        <ErrorAlert error={save.error} />
        {save.isSuccess && test !== undefined && <Notice>{t.tests.form.saved}</Notice>}
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
