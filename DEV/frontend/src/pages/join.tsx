import { useMutation, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router';
import { api, ApiError, type Me } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { ErrorAlert, Field, formValues, Icon, Loading, order } from '../components/ui';
import { AuthLayout } from './auth';

function RegisterForm({ code }: { code: string }) {
  const { t } = useI18n();
  const [ageGroup, setAgeGroup] = useState<'18_plus' | 'under_18' | ''>('');
  const register = useMutation({
    mutationFn: async (v: Record<string, string>) => {
      await api.post(`/api/invites/${encodeURIComponent(code)}/register`, {
        email: v.email,
        password: v.password,
        displayName: v.displayName,
        ageGroup,
        consents: { core_learning: v.core === 'on', ai_processing: v.ai === 'on' },
      });
      await api.post('/api/auth/login', { email: v.email, password: v.password });
    },
    onSuccess: () => window.location.assign('/home'),
  });
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    register.mutate(formValues(e.currentTarget));
  };
  const under18 = ageGroup === 'under_18';

  return (
    <form className="stack in" style={order(2)} onSubmit={submit}>
      <ErrorAlert error={register.error} />
      <fieldset>
        <legend>{t.join.age}</legend>
        <div className="choice-row">
          <label className="choice">
            <input type="radio" name="age" required onChange={() => setAgeGroup('18_plus')} />
            <span className="choice-dot" aria-hidden="true" />
            <span>{t.join.adult}</span>
          </label>
          <label className="choice">
            <input type="radio" name="age" onChange={() => setAgeGroup('under_18')} />
            <span className="choice-dot" aria-hidden="true" />
            <span>{t.join.minor}</span>
          </label>
        </div>
      </fieldset>
      {under18 && <p className="alert" role="alert"><span>{t.join.minorBlocked}</span></p>}
      {ageGroup === '18_plus' && (
        <div className="stack unfold">
          <Field label={t.common.fullName} name="displayName" autoComplete="name" type="text" maxLength={120} required />
          <Field label={t.common.email} name="email" type="email" autoComplete="username" required />
          <Field label={t.common.password} name="password" type="password" autoComplete="new-password" minLength={8} maxLength={200} hint={t.common.minPassword} required />
          <label className="check">
            <input type="checkbox" name="core" required />
            <span className="check-box" aria-hidden="true"><Icon name="check" size={14} /></span>
            <span>{t.join.agree} <span className="muted">{t.consent.terms}</span></span>
          </label>
          <label className="check">
            <input type="checkbox" name="ai" />
            <span className="check-box" aria-hidden="true"><Icon name="check" size={14} /></span>
            <span>{t.join.optional} <span className="muted">{t.consent.ai}</span></span>
          </label>
          <button className="primary-wide" disabled={register.isPending}>
            <span>{register.isPending ? t.join.creating : t.join.createAndJoin}</span>
            <Icon name="arrow" />
          </button>
        </div>
      )}
    </form>
  );
}

function JoinExisting({ code }: { code: string }) {
  const { t } = useI18n();
  const join = useMutation({
    mutationFn: () => api.post(`/api/invites/${encodeURIComponent(code)}/join`),
    onSuccess: () => window.location.assign('/home'),
  });
  return (
    <div className="stack in" style={order(2)}>
      <ErrorAlert error={join.error} />
      <button className="primary-wide" onClick={() => join.mutate()} disabled={join.isPending}>
        <span>{t.join.joinClass}</span>
        <Icon name="arrow" />
      </button>
    </div>
  );
}

export function JoinPage() {
  const { t } = useI18n();
  const code = useParams().code ?? '';
  const invite = useQuery({
    queryKey: ['invite', code],
    queryFn: () => api.get<{ className: string }>(`/api/invites/${encodeURIComponent(code)}`),
  });
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<Me>('/api/me').catch((err) => {
      if (err instanceof ApiError && err.status === 401) return null;
      throw err;
    }),
  });

  const layout = t.join.art;

  if (invite.isPending || me.isPending) return <AuthLayout {...layout}><Loading /></AuthLayout>;
  if (invite.isError) {
    return (
      <AuthLayout {...layout}>
        <h1 className="in" style={order(1)}>{t.join.cannotJoin}</h1>
        <ErrorAlert error={invite.error} />
        <p className="muted">{t.join.askNewCode}</p>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout {...layout}>
      <p className="eyebrow in" style={order(0)}>{t.join.invited}</p>
      <h1 className="in" style={order(1)}>{t.join.title(invite.data.className)}</h1>
      {me.data?.role === 'student' && <JoinExisting code={code} />}
      {me.data && me.data.role !== 'student' && <p className="alert" role="alert"><span>{t.join.studentOnly}</span></p>}
      {!me.data && (
        <>
          <RegisterForm code={code} />
          <p className="muted">{t.join.haveAccount} <Link to="/login" state={{ from: `/join/${code}` }} viewTransition>{t.join.login}</Link></p>
        </>
      )}
    </AuthLayout>
  );
}
