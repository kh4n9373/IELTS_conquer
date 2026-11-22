import { useMutation } from '@tanstack/react-query';
import type { FormEvent, ReactNode } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router';
import { api } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { Brand, ErrorAlert, Field, formValues, Icon, LangSwitch, Notice, order } from '../components/ui';

/** Black page on one side, working form on the other. Also used by the join flow. */
export function AuthLayout({ line, accent, note, children }: { line: string; accent: string; note: string; children: ReactNode }) {
  return (
    <div className="auth">
      <aside className="auth-art">
        <Brand to="/" />
        <p className="auth-line" aria-hidden="true">
          <span className="in" style={order(0)}>{line}</span>{' '}
          <span className="in auth-accent" style={order(2)}><em>{accent}</em></span>
        </p>
        <div className="auth-sheet in" style={order(4)} aria-hidden="true">
          <span className="auth-sheet-rule" />
          <p>Most visitors come for the food, but many stay to talk.</p>
          <p>For older residents, the market is <mark>the main place to meet friends</mark> during the week.</p>
          <span className="auth-note">{note}</span>
        </div>
      </aside>
      <main className="auth-main">
        <div className="auth-top">
          <div className="auth-brand"><Brand to="/" /></div>
          <LangSwitch />
        </div>
        <div className="auth-column">{children}</div>
      </main>
    </div>
  );
}

export function LoginPage() {
  const { t } = useI18n();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;
  const login = useMutation({
    mutationFn: (body: Record<string, string>) => api.post('/api/auth/login', body),
    // Full reload so no cached data from a previous account survives.
    onSuccess: () => window.location.assign(from?.startsWith('/') && !from.startsWith('//') ? from : '/'),
  });
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    login.mutate(formValues(e.currentTarget));
  };
  return (
    <AuthLayout {...t.auth.loginArt}>
      <h1 className="in" style={order(1)}>{t.auth.login}</h1>
      <p className="muted in" style={order(2)}>{t.auth.loginHint}</p>
      <form className="stack in" style={order(3)} onSubmit={submit}>
        <ErrorAlert error={login.error} />
        <Field label={t.common.email} name="email" type="email" autoComplete="username" required />
        <Field label={t.common.password} name="password" type="password" autoComplete="current-password" required />
        <button className="primary-wide" disabled={login.isPending}>
          <span>{login.isPending ? t.auth.loggingIn : t.auth.login}</span>
          <Icon name="arrow" />
        </button>
        <Link className="quiet-link" to="/forgot-password" viewTransition>{t.auth.forgotLink}</Link>
      </form>
    </AuthLayout>
  );
}

export function ForgotPage() {
  const { t } = useI18n();
  const forgot = useMutation({ mutationFn: (body: Record<string, string>) => api.post('/api/auth/forgot', body) });
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    forgot.mutate(formValues(e.currentTarget));
  };
  return (
    <AuthLayout {...t.auth.forgotArt}>
      <h1 className="in" style={order(1)}>{t.auth.forgotTitle}</h1>
      <p className="muted in" style={order(2)}>{t.auth.forgotHint}</p>
      <form className="stack in" style={order(3)} onSubmit={submit}>
        <ErrorAlert error={forgot.error} />
        {forgot.isSuccess && <Notice>{t.auth.forgotSent}</Notice>}
        <Field label={t.common.email} name="email" type="email" autoComplete="username" required />
        <button className="primary-wide" disabled={forgot.isPending}>
          <span>{t.auth.sendLink}</span>
          <Icon name="arrow" />
        </button>
        <Link className="quiet-link" to="/login" viewTransition>{t.auth.backToLogin}</Link>
      </form>
    </AuthLayout>
  );
}

export function ResetPage() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const token = params.get('token') ?? '';
  const reset = useMutation({ mutationFn: (password: string) => api.post('/api/auth/reset', { token, password }) });
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    reset.mutate(formValues(e.currentTarget).password ?? '');
  };
  return (
    <AuthLayout {...t.auth.resetArt}>
      <h1 className="in" style={order(1)}>{t.auth.resetTitle}</h1>
      {reset.isSuccess ? (
        <div className="stack in" style={order(2)}>
          <Notice>{t.auth.resetDone}</Notice>
          <Link className="button-link" to="/login" viewTransition>
            <span>{t.auth.login}</span>
            <Icon name="arrow" />
          </Link>
        </div>
      ) : (
        <form className="stack in" style={order(2)} onSubmit={submit}>
          <ErrorAlert error={reset.error} />
          <Field label={t.auth.newPassword} name="password" type="password" autoComplete="new-password" minLength={8} maxLength={200} hint={t.common.minPassword} required />
          <button className="primary-wide" disabled={reset.isPending || !token}>
            <span>{t.auth.savePassword}</span>
            <Icon name="arrow" />
          </button>
        </form>
      )}
    </AuthLayout>
  );
}
