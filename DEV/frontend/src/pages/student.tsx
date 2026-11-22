import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type ClassSummary, type Consents } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { useFreshItems } from '../lib/motion';
import type { Copy } from '../locales/vi';
import { useMe } from '../app/session';
import { Empty, ErrorAlert, Icon, Loading, order, PageHead, type IconName } from '../components/ui';

const AREAS: [keyof Copy['student']['areas'], IconName][] = [
  ['exam', 'exam'],
  ['analysis', 'chart'],
  ['practice', 'pen'],
  ['teacher', 'chat'],
];

export function StudentHomePage() {
  const me = useMe();
  const { t } = useI18n();
  const classes = useQuery({ queryKey: ['classes'], queryFn: () => api.get<ClassSummary[]>('/api/classes') });
  const listRef = useFreshItems<HTMLUListElement>(classes.data?.map((c) => c.id).join() ?? '');
  return (
    <>
      <PageHead eyebrow={me.data ? t.student.hello(me.data.displayName) : undefined} title={t.student.title} lede={t.student.lede} />
      <section className="covers-wrap in" style={order(3)}>
        <div className="section-head"><h2>{t.student.yourClasses}</h2></div>
        <ErrorAlert error={classes.error} />
        {classes.isPending && <Loading />}
        {classes.data?.length === 0 && <Empty title={t.student.noClassTitle}>{t.student.noClassBody}</Empty>}
        <ul className="covers" ref={listRef}>
          {classes.data?.map((c) => (
            <li key={c.id} data-key={c.id} className={`cover${c.status === 'archived' ? ' is-archived' : ''}`}>
              <span className="cover-label" aria-hidden="true">IELTS · Reading</span>
              <span className="cover-name">{c.name}</span>
              {c.status === 'archived' && <span className="tag">{t.common.archived}</span>}
            </li>
          ))}
        </ul>
      </section>
      <section className="soon in" style={order(4)} aria-labelledby="soon-title">
        <div className="section-head">
          <h2 id="soon-title">{t.student.soonTitle}</h2>
          <span className="hand-note">{t.student.soonNote}</span>
        </div>
        <ul className="soon-list">
          {AREAS.map(([key, icon]) => (
            <li key={key}>
              <Icon name={icon} size={22} />
              <strong>{t.student.areas[key].title}</strong>
              <span className="muted">{t.student.areas[key].body}</span>
              <span className="tag">{t.common.soon}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

export function SettingsPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const consents = useQuery({ queryKey: ['consents'], queryFn: () => api.get<Consents>('/api/consents') });
  const update = useMutation({
    mutationFn: (body: { purpose: 'core_learning' | 'ai_processing'; granted: boolean }) => api.post('/api/consents', body),
    onSuccess: () => client.invalidateQueries({ queryKey: ['consents'] }),
  });
  return (
    <>
      <PageHead eyebrow={t.settings.eyebrow} title={t.settings.title} lede={t.settings.lede} />
      <section className="consent in" style={order(3)}>
        <div className="section-head"><h2>{t.settings.consent}</h2></div>
        <div className="stack">
          <ErrorAlert error={consents.error ?? update.error} />
          {consents.isPending && <Loading />}
          {consents.data && (
            <>
              <p className="muted">{t.consent.terms} {t.settings.version} {consents.data.termsVersion}</p>
              <label className={`switch-row${consents.data.current.ai_processing ? ' is-on' : ''}`}>
                <span className="switch-copy">
                  <span className="switch-state">{consents.data.current.ai_processing ? t.settings.on : t.settings.off}</span>
                  <span>{t.consent.ai}</span>
                </span>
                <span className="switch">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={consents.data.current.ai_processing}
                    disabled={update.isPending}
                    onChange={(e) => update.mutate({ purpose: 'ai_processing', granted: e.target.checked })}
                  />
                  <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
                </span>
              </label>
            </>
          )}
        </div>
      </section>
    </>
  );
}
