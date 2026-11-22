import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { FormEvent } from 'react';
import { api, type Teacher } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { useFreshItems } from '../lib/motion';
import { Empty, ErrorAlert, Field, formValues, Icon, Loading, Notice, order, PageHead, Section } from '../components/ui';

export function AdminPage() {
  const { t } = useI18n();
  const client = useQueryClient();
  const teachers = useQuery({ queryKey: ['teachers'], queryFn: () => api.get<Teacher[]>('/api/admin/teachers') });
  const refresh = () => client.invalidateQueries({ queryKey: ['teachers'] });
  const create = useMutation({
    mutationFn: (body: Record<string, string>) => api.post<Teacher>('/api/admin/teachers', body),
    onSuccess: refresh,
  });
  const setLock = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'lock' | 'unlock' }) => api.post(`/api/admin/teachers/${id}/${action}`),
    onSuccess: refresh,
  });
  const listRef = useFreshItems<HTMLUListElement>(teachers.data?.map((x) => x.id).join() ?? '');
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    create.mutate(formValues(form), { onSuccess: () => form.reset() });
  };
  const active = teachers.data?.filter((x) => x.status === 'active').length ?? 0;
  const locked = (teachers.data?.length ?? 0) - active;

  return (
    <>
      <PageHead eyebrow={t.admin.eyebrow} title={t.admin.title}>
        {teachers.data && (
          <dl className="tally">
            <div><dt>{t.admin.active}</dt><dd>{active}</dd></div>
            <div><dt>{t.admin.locked}</dt><dd>{locked}</dd></div>
          </dl>
        )}
      </PageHead>
      <div className="split">
        <section className="roster in" style={order(3)}>
          <div className="section-head"><h2>{t.admin.list}</h2></div>
          <ErrorAlert error={teachers.error ?? setLock.error} />
          {teachers.isPending && <Loading />}
          {teachers.data?.length === 0 && <Empty title={t.admin.emptyTitle}>{t.admin.emptyBody}</Empty>}
          <ul className="people" ref={listRef}>
            {teachers.data?.map((teacher) => {
              const isActive = teacher.status === 'active';
              const verb = isActive ? t.admin.lock : t.admin.unlock;
              return (
                <li key={teacher.id} data-key={teacher.id} className={isActive ? undefined : 'is-locked'}>
                  <span className="person-initial" aria-hidden="true">{teacher.displayName.trim().charAt(0).toUpperCase()}</span>
                  <span className="grow">
                    <span className="person-name">{teacher.displayName}</span>
                    <span className="muted">{teacher.email}</span>
                  </span>
                  <span className={`tag${isActive ? ' tag-live' : ''}`}>{isActive ? t.admin.active : t.admin.locked}</span>
                  <button
                    className={isActive ? 'danger ghost' : 'secondary'}
                    aria-label={`${verb} ${teacher.displayName}`}
                    disabled={setLock.isPending}
                    onClick={() => setLock.mutate({ id: teacher.id, action: isActive ? 'lock' : 'unlock' })}
                  >
                    <Icon name={isActive ? 'lock' : 'unlock'} />
                    <span>{verb}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
        <aside className="side-sheet in" style={order(4)}>
          <Section title={t.admin.newAccount}>
            <form className="stack" onSubmit={submit}>
              <ErrorAlert error={create.error} />
              {create.data && (create.data.activationSent
                ? <Notice>{t.admin.sent(create.data.email)}</Notice>
                : <p className="alert" role="status"><span>{t.admin.notSent(create.data.email)}</span></p>
              )}
              <Field label={t.common.fullName} name="displayName" type="text" maxLength={120} required />
              <Field label={t.common.email} name="email" type="email" required />
              <button className="primary-wide" disabled={create.isPending}>
                <span>{t.admin.create}</span>
                <Icon name="plus" />
              </button>
            </form>
          </Section>
        </aside>
      </div>
    </>
  );
}
