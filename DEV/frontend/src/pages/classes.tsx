import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router';
import { api, type ClassDetail, type ClassSummary, type Invite } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { useFreshItems } from '../lib/motion';
import { Disclosure, Empty, ErrorAlert, Field, formValues, Icon, Loading, order, PageHead, Section, useConfirm } from '../components/ui';

const sharedTitle = (id: string) => ({ viewTransitionName: `class-${id}` });

export function ClassesPage() {
  const { t, formatDate } = useI18n();
  const client = useQueryClient();
  const classes = useQuery({ queryKey: ['classes'], queryFn: () => api.get<ClassSummary[]>('/api/classes') });
  const create = useMutation({
    mutationFn: (name: string) => api.post<ClassSummary>('/api/classes', { name }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['classes'] }),
  });
  const listRef = useFreshItems<HTMLOListElement>(classes.data?.map((c) => c.id).join() ?? '');
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    create.mutate(formValues(form).name ?? '', { onSuccess: () => form.reset() });
  };
  const activeCount = classes.data?.filter((c) => c.status === 'active').length ?? 0;

  return (
    <>
      <PageHead
        eyebrow={t.classes.eyebrow}
        title={t.classes.title}
        lede={classes.data?.length ? t.classes.summary(activeCount, classes.data.length - activeCount) : t.classes.emptyLede}
      />
      <div className="split">
        <section className="toc-wrap in" style={order(3)}>
          <div className="section-head"><h2>{t.classes.list}</h2></div>
          <ErrorAlert error={classes.error} />
          {classes.isPending && <Loading />}
          {classes.data?.length === 0 && <Empty title={t.classes.emptyTitle}>{t.classes.emptyBody}</Empty>}
          <ol className="toc" ref={listRef}>
            {classes.data?.map((c, i) => (
              <li key={c.id} data-key={c.id}>
                <Link className="toc-row" to={`/classes/${c.id}`} viewTransition>
                  <span className="toc-no" aria-hidden="true">{String(i + 1).padStart(2, '0')}</span>
                  <span className="toc-name"><span style={sharedTitle(c.id)}>{c.name}</span></span>
                  <span className="toc-leader" aria-hidden="true" />
                  {c.status === 'archived' && <span className="tag">{t.common.archived}</span>}
                  <span className="toc-date">{formatDate(c.createdAt)}</span>
                  <span className="toc-arrow" aria-hidden="true"><Icon name="arrow" /></span>
                </Link>
              </li>
            ))}
          </ol>
        </section>
        <aside className="side-sheet in" style={order(4)}>
          <Section title={t.classes.create}>
            <form className="stack" onSubmit={submit}>
              <ErrorAlert error={create.error} />
              <Field label={t.classes.name} name="name" type="text" maxLength={120} required />
              <button className="primary-wide" disabled={create.isPending}>
                <span>{create.isPending ? t.classes.creating : t.classes.create}</span>
                <Icon name="plus" />
              </button>
            </form>
          </Section>
        </aside>
      </div>
    </>
  );
}

function InviteRow({ invite, onDisable, busy }: { invite: Invite; onDisable: () => void; busy: boolean }) {
  const { t, formatDate } = useI18n();
  const [copy, setCopy] = useState<'idle' | 'done' | 'failed'>('idle');
  const timer = useRef<number | undefined>(undefined);
  const link = `${window.location.origin}/join/${invite.code}`;
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copyLink = async () => {
    window.clearTimeout(timer.current);
    try {
      await navigator.clipboard.writeText(link);
      setCopy('done');
    } catch {
      setCopy('failed');
    }
    timer.current = window.setTimeout(() => setCopy('idle'), 2400);
  };
  return (
    <li className="ticket" data-key={invite.id}>
      <div className="ticket-main">
        <span className="code">{invite.code}</span>
        <span className="muted">{t.classes.expires(formatDate(invite.expiresAt))}</span>
      </div>
      <div className="ticket-actions">
        <button className={`secondary copy-btn${copy === 'done' ? ' is-done' : ''}`} onClick={copyLink}>
          <Icon name={copy === 'done' ? 'check' : 'copy'} />
          <span aria-live="polite">{copy === 'done' ? t.classes.copied : copy === 'failed' ? t.classes.copyFailed : t.classes.copyLink}</span>
        </button>
        <button className="danger" onClick={onDisable} disabled={busy} aria-label={t.classes.disableLabel(invite.code)}>{t.classes.disable}</button>
      </div>
    </li>
  );
}

export function ClassDetailPage() {
  const { t, formatDate } = useI18n();
  const id = useParams().id ?? '';
  const client = useQueryClient();
  const detail = useQuery({ queryKey: ['class', id], queryFn: () => api.get<ClassDetail>(`/api/classes/${id}`) });
  const refresh = () => {
    client.invalidateQueries({ queryKey: ['class', id] });
    client.invalidateQueries({ queryKey: ['classes'] });
  };
  const action = useMutation({ mutationFn: (run: () => Promise<unknown>) => run(), onSuccess: refresh });
  const { ask, dialog } = useConfirm();
  const memberRef = useFreshItems<HTMLUListElement>(detail.data?.members.map((m) => m.id).join() ?? '');
  const inviteRef = useFreshItems<HTMLUListElement>(detail.data?.invites.map((i) => i.id).join() ?? '');

  if (detail.isPending) return <Loading />;
  if (detail.isError) return <ErrorAlert error={detail.error} />;
  const cls = detail.data;
  const active = cls.status === 'active';

  const rename = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const name = formValues(e.currentTarget).name;
    action.mutate(() => api.patch(`/api/classes/${id}`, { name }));
  };

  return (
    <>
      <Link className="back-link in" style={order(0)} to="/classes" viewTransition>
        <Icon name="back" />
        <span>{t.classes.all}</span>
      </Link>
      <PageHead
        eyebrow={active ? t.classes.createdOn(formatDate(cls.createdAt)) : t.classes.archivedClass}
        title={cls.name}
        titleStyle={sharedTitle(id)}
      >
        {active ? (
          <button
            className="danger"
            disabled={action.isPending}
            onClick={() => ask({
              title: t.classes.archiveAsk,
              body: t.classes.archiveBody,
              action: t.classes.archive,
              run: () => action.mutate(() => api.post(`/api/classes/${id}/archive`)),
            })}
          >
            <Icon name="archive" />
            <span>{t.classes.archive}</span>
          </button>
        ) : <span className="tag">{t.common.archived}</span>}
      </PageHead>
      <ErrorAlert error={action.error} />
      <div className={active ? 'split' : ''}>
        <section className="roster in" style={order(3)}>
          <div className="section-head">
            <h2>{t.classes.students(cls.members.length)}</h2>
          </div>
          {cls.members.length === 0 && <Empty title={t.classes.noStudentsTitle}>{t.classes.noStudentsBody}</Empty>}
          <ul className="people" ref={memberRef}>
            {cls.members.map((m) => (
              <li key={m.id} data-key={m.id}>
                <span className="person-initial" aria-hidden="true">{m.displayName.trim().charAt(0).toUpperCase()}</span>
                <span className="grow">
                  <span className="person-name">{m.displayName}</span>
                  <span className="muted">{m.email}</span>
                </span>
                <button
                  className="danger ghost"
                  aria-label={t.classes.removeLabel(m.displayName)}
                  disabled={action.isPending}
                  onClick={() => ask({
                    title: t.classes.removeAsk(m.displayName),
                    body: t.classes.removeBody,
                    action: t.classes.remove,
                    run: () => action.mutate(() => api.delete(`/api/classes/${id}/members/${m.id}`)),
                  })}
                >
                  {t.classes.remove}
                </button>
              </li>
            ))}
          </ul>
        </section>
        {active && (
          <aside className="side-sheet in" style={order(4)}>
            <Section title={t.classes.invites}>
              <div className="stack">
                {cls.invites.length === 0 && <p className="muted">{t.classes.noInvites}</p>}
                <ul className="tickets" ref={inviteRef}>
                  {cls.invites.map((invite) => (
                    <InviteRow key={invite.id} invite={invite} busy={action.isPending} onDisable={() => action.mutate(() => api.post(`/api/invites/${invite.id}/disable`))} />
                  ))}
                </ul>
                <button className={cls.invites.length ? 'secondary' : 'primary-wide'} disabled={action.isPending} onClick={() => action.mutate(() => api.post(`/api/classes/${id}/invites`, { expiresInDays: 14 }))}>
                  <span>{t.classes.newInvite}</span>
                  <Icon name="plus" />
                </button>
              </div>
            </Section>
            <Disclosure label={t.classes.rename} icon="pencil">
              <form className="stack" onSubmit={rename} key={cls.name}>
                <Field label={t.classes.name} name="name" type="text" defaultValue={cls.name} maxLength={120} required />
                <button className="secondary" disabled={action.isPending}>{t.common.save}</button>
              </form>
            </Disclosure>
          </aside>
        )}
      </div>
      {dialog}
    </>
  );
}
