import { useCallback, useEffect, useId, useRef, useState, type CSSProperties, type InputHTMLAttributes, type ReactNode } from 'react';
import { Link } from 'react-router';
import logo from '../assets/logo.png';
import { useI18n, type Lang } from '../lib/i18n';
import { errorText } from '../lib/messages';
import { gsap, reducedMotion } from '../lib/motion';

/** Position in a page's entrance sequence. */
export const order = (i: number) => ({ '--i': i }) as CSSProperties;

const ICONS = {
  arrow: <path d="M4 12h15m-6-6 6 6-6 6" />,
  back: <path d="M20 12H5m6-6-6 6 6 6" />,
  check: <path d="m5 12.5 4.5 4.5L19 7" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="1.5" /><path d="M5 15H4V4h11v1" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  lock: <><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></>,
  unlock: <><rect x="5" y="11" width="14" height="9" rx="1.5" /><path d="M8 11V8a4 4 0 0 1 7.5-2" /></>,
  archive: <><path d="M4 5h16v4H4zM6 9v10h12V9" /><path d="M10 13h4" /></>,
  pencil: <path d="m5 19 1-4L16 5l3 3L9 18l-4 1Z" />,
  out: <path d="M10 4H5v16h5m1-8h9m-3-3 3 3-3 3" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  book: <path d="M12 6c-2-1.5-5-2-8-2v14c3 0 6 .5 8 2 2-1.5 5-2 8-2V4c-3 0-6 .5-8 2Zm0 0v14" />,
  users: <><circle cx="9" cy="8" r="3.5" /><path d="M3 20c.5-4 3-6 6-6s5.5 2 6 6m1-15a3.5 3.5 0 0 1 0 6.5m2 3c1.7.8 2.7 2.8 3 5.5" /></>,
  settings: <path d="M4 7h9m4 0h3M4 17h3m4 0h9M15 5v4M9 15v4" />,
  chat: <path d="M4 5h16v11H10l-4 4v-4H4V5Z" />,
  pen: <path d="M4 20h5L20 9l-5-5L4 15v5Zm9-14 5 5" />,
  exam: <><path d="M6 3h9l4 4v14H6V3Z" /><path d="m9.5 13 2 2 3.5-4" /></>,
  chart: <path d="M4 20V4m0 16h16M8 16v-4m5 4V8m5 8v-6" />,
} as const;

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

export function Brand({ to }: { to: string }) {
  return (
    <Link className="brand" to={to} viewTransition aria-label="PANXG English">
      <img src={logo} alt="" width="320" height="64" />
    </Link>
  );
}

const LANGS: [Lang, string, string][] = [['vi', 'VI', 'Tiếng Việt'], ['en', 'EN', 'English']];

export function LangSwitch() {
  const { lang, setLang, t } = useI18n();
  return (
    <div className="lang" role="group" aria-label={t.common.language}>
      {LANGS.map(([code, short, name]) => (
        <button key={code} type="button" lang={code} className={lang === code ? 'on' : undefined} aria-pressed={lang === code} aria-label={name} onClick={() => setLang(code)}>
          {short}
        </button>
      ))}
    </div>
  );
}

/** Google photo when there is one, else the first letter of the name. */
export function Avatar({ name, url, size = 40 }: { name: string; url?: string | null; size?: number }) {
  const [broken, setBroken] = useState(false);
  const style = { width: size, height: size, fontSize: size * 0.45 } as CSSProperties;
  if (url && !broken) {
    // Google's image host refuses requests that carry a referrer from another site.
    return <img className="avatar" src={url} alt="" style={style} referrerPolicy="no-referrer" onError={() => setBroken(true)} />;
  }
  return <span className="avatar avatar-initial" style={style} aria-hidden="true">{name.trim().charAt(0).toUpperCase() || '?'}</span>;
}

export function Field({ label, hint, ...props }: { label: string; hint?: string } & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <span className="input-wrap">
        <input id={id} aria-describedby={hint ? `${id}-hint` : undefined} {...props} />
      </span>
      {hint && <span id={`${id}-hint`} className="muted">{hint}</span>}
    </div>
  );
}

export function ErrorAlert({ error }: { error: unknown }) {
  const { t } = useI18n();
  if (!error) return null;
  return (
    <p className="alert" role="alert">
      <svg className="alert-scribble" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg>
      <span>{errorText(error, t.errors)}</span>
    </p>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return (
    <p className="notice" role="status">
      <Icon name="check" />
      <span>{children}</span>
    </p>
  );
}

export function Loading() {
  const { t } = useI18n();
  return (
    <div className="loading" role="status">
      <span className="loading-lines" aria-hidden="true"><i /><i /><i /></span>
      <span className="muted">{t.common.loading}</span>
    </div>
  );
}

export function PageLoader({ bare = false }: { bare?: boolean }) {
  const { t } = useI18n();
  return (
    <div className="page-loader" role="status">
      {!bare && <img className="page-loader-logo" src={logo} alt="" />}
      <span className="sr-only">{t.common.loading}</span>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <svg viewBox="0 0 96 112" width="72" height="84" fill="none" aria-hidden="true">
        <path className="empty-sheet" d="M14 6h50l20 20v80H14V6Z" />
        <path className="empty-sheet" d="M64 6v20h20" />
        <path className="empty-line" d="M28 50h42M28 64h42M28 78h24" />
      </svg>
      <p className="empty-title">{title}</p>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <div className="section-head">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function PageHead({ eyebrow, title, lede, titleStyle, children }: {
  eyebrow?: ReactNode;
  title: string;
  lede?: ReactNode;
  titleStyle?: CSSProperties;
  children?: ReactNode;
}) {
  return (
    <header className="head">
      <div className="head-copy">
        {eyebrow && <p className="eyebrow in" style={order(0)}>{eyebrow}</p>}
        <h1 className="in" style={{ ...order(1), ...titleStyle }}>{title}</h1>
        {lede && <p className="lede in" style={order(2)}>{lede}</p>}
      </div>
      {children && <div className="head-actions in" style={order(3)}>{children}</div>}
    </header>
  );
}

/** Height-animated reveal. Closed content is inert, so it cannot take focus. */
export function Disclosure({ label, icon, children }: { label: string; icon: IconName; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className={`disclosure${open ? ' is-open' : ''}`}>
      <button type="button" className="disclosure-toggle" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <Icon name={icon} />
        <span>{label}</span>
        <span className="disclosure-sign" aria-hidden="true" />
      </button>
      <div className="disclosure-body" id={id} inert={!open}>
        <div className="disclosure-inner">{children}</div>
      </div>
    </div>
  );
}

export type ConfirmRequest = { title: string; body: string; action: string; run: () => void; tone?: 'danger' | 'go' };

/** Accessible replacement for window.confirm. Render `dialog` once per page. */
export function useConfirm() {
  const [request, setRequest] = useState<ConfirmRequest | null>(null);
  const ref = useRef<HTMLDialogElement>(null);
  const { t } = useI18n();

  useEffect(() => {
    const el = ref.current;
    if (!request || !el || el.open) return;
    el.showModal();
    if (!reducedMotion()) {
      gsap.fromTo(el, { autoAlpha: 0, y: 28, rotate: -1.2, scale: 0.97 }, { autoAlpha: 1, y: 0, rotate: 0, scale: 1, duration: 0.55, clearProps: 'all' });
    }
  }, [request]);

  const close = useCallback((after?: () => void) => {
    const el = ref.current;
    const finish = () => {
      el?.close();
      setRequest(null);
      after?.();
    };
    if (!el || reducedMotion()) return finish();
    gsap.to(el, { autoAlpha: 0, y: 14, scale: 0.98, duration: 0.2, ease: 'power2.in', onComplete: () => { gsap.set(el, { clearProps: 'all' }); finish(); } });
  }, []);

  const dialog = (
    <dialog
      ref={ref}
      className="confirm"
      aria-labelledby="confirm-title"
      onCancel={(e) => { e.preventDefault(); close(); }}
    >
      {request && (
        <>
          <p className="eyebrow">{t.common.confirm}</p>
          <h2 id="confirm-title">{request.title}</h2>
          <p className="muted">{request.body}</p>
          <div className="confirm-actions">
            <button type="button" className="secondary" onClick={() => close()}>{t.common.cancel}</button>
            <button type="button" className={request.tone === 'go' ? undefined : 'danger-solid'} autoFocus={request.tone === 'go'} onClick={() => close(request.run)}>{request.action}</button>
          </div>
        </>
      )}
    </dialog>
  );

  return { ask: setRequest, dialog };
}

/** Reads a form's named inputs as strings. */
export const formValues = (form: HTMLFormElement) =>
  Object.fromEntries(new FormData(form).entries()) as Record<string, string>;
