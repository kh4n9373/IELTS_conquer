import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { FormEvent } from 'react';
import { useMe } from '../app/session';
import { api, type ClassSummary, type Me } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { Avatar, ErrorAlert, Field, formValues, Icon, Loading, Notice, order, Section } from '../components/ui';

const BANDS = Array.from({ length: 17 }, (_, i) => (1 + i * 0.5).toFixed(1));
const band = (value: string | undefined) => (value ? Number(value) : null);

function BandField({ name, label, value }: { name: string; label: string; value: number | null }) {
  const { t } = useI18n();
  return (
    <div className="field">
      <label htmlFor={name}>{label}</label>
      <select id={name} name={name} defaultValue={value == null ? '' : value.toFixed(1)}>
        <option value="">{t.onboarding.bandUnknown}</option>
        {BANDS.map((b) => <option key={b} value={b}>{b}</option>)}
      </select>
    </div>
  );
}

function StudentClasses() {
  const { t } = useI18n();
  const classes = useQuery({ queryKey: ['classes'], queryFn: () => api.get<ClassSummary[]>('/api/classes') });
  return (
    <Section title={t.profile.classes}>
      <ErrorAlert error={classes.error} />
      {classes.isPending && <Loading />}
      {classes.data?.length === 0 && <p className="muted">{t.profile.noClasses}</p>}
      {!!classes.data?.length && (
        <ul className="profile-classes">
          {classes.data.map((c) => <li key={c.id}>{c.name}</li>)}
        </ul>
      )}
    </Section>
  );
}

export function ProfilePage() {
  const { t, lang } = useI18n();
  const me = useMe();
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (v: Record<string, string>) =>
      api.patch<Me>('/api/me', {
        displayName: v.displayName,
        phone: v.phone || null,
        currentBand: band(v.currentBand),
        targetBand: band(v.targetBand),
      }),
    onSuccess: (data) => client.setQueryData(['me'], data),
  });
  if (!me.data) return <Loading />;
  const user = me.data;
  const copy = t.profile;
  const fmt = (n: number | null) => (n == null ? '?' : n.toFixed(1));
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    save.mutate(formValues(e.currentTarget));
  };

  return (
    <>
      <header className="profile-head in" style={order(0)}>
        <Avatar name={user.displayName} url={user.avatarUrl} size={88} />
        <div className="grow">
          <p className="eyebrow">{copy.eyebrow} · {t.roles[user.role]}</p>
          <h1>{user.displayName}</h1>
          <p className="muted">{user.email}</p>
        </div>
      </header>
      <div className="split">
        <section className="in" style={order(2)}>
          <div className="section-head"><h2>{copy.info}</h2></div>
          <dl className="profile-facts">
            <div><dt>{copy.signIn}</dt><dd>{copy.methods[user.signInMethod]}</dd></div>
            <div><dt>{copy.joined}</dt><dd>{new Date(user.createdAt).toLocaleDateString(lang === 'vi' ? 'vi-VN' : 'en-GB')}</dd></div>
            <div><dt>{t.onboarding.phone}</dt><dd>{user.phone ?? copy.none}</dd></div>
            <div><dt>{copy.referral}</dt><dd>{user.referralSource ? t.onboarding.referrals[user.referralSource] : copy.none}</dd></div>
            {user.role === 'student' && (
              <div>
                <dt>{copy.goal}</dt>
                <dd>{user.currentBand == null && user.targetBand == null ? copy.none : copy.bands(fmt(user.currentBand), fmt(user.targetBand))}</dd>
              </div>
            )}
          </dl>
          {user.role === 'student' && <StudentClasses />}
        </section>
        <aside className="side-sheet in" style={order(3)}>
          <Section title={copy.edit}>
            <form className="stack" onSubmit={submit} key={`${user.displayName}|${user.phone}|${user.currentBand}|${user.targetBand}`}>
              <ErrorAlert error={save.error} />
              {save.isSuccess && <Notice>{copy.saved}</Notice>}
              <Field label={t.common.fullName} name="displayName" type="text" maxLength={120} defaultValue={user.displayName} required />
              <Field label={t.onboarding.phone} name="phone" type="tel" maxLength={20} defaultValue={user.phone ?? ''} hint={t.onboarding.phoneHint} />
              {user.role === 'student' && (
                <div className="pair">
                  <BandField name="currentBand" label={t.onboarding.currentBand} value={user.currentBand} />
                  <BandField name="targetBand" label={t.onboarding.targetBand} value={user.targetBand} />
                </div>
              )}
              <button className="primary-wide" disabled={save.isPending}>
                <span>{t.common.save}</span>
                <Icon name="check" />
              </button>
            </form>
          </Section>
        </aside>
      </div>
    </>
  );
}
