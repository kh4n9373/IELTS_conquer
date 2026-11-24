import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useLayoutEffect, useRef } from 'react';
import { Navigate, NavLink, Outlet, useLocation } from 'react-router';
import { api, type Me } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { gsap, reducedMotion } from '../lib/motion';
import type { Copy } from '../locales/vi';
import { homeFor, useMe } from './session';
import { Brand, ErrorAlert, Icon, LangSwitch, PageLoader, type IconName } from '../components/ui';

/** Only screens that exist today get a tab. Upcoming areas are listed on the student home. */
const TABS: Record<Me['role'], [string, keyof Copy['nav'], IconName][]> = {
  admin: [['/admin', 'teachers', 'users']],
  teacher: [['/classes', 'classes', 'book'], ['/tests', 'tests', 'exam'], ['/assignments', 'assignments', 'pen']],
  student: [['/home', 'home', 'book'], ['/assignments', 'myWork', 'exam']],
};

/** The red block glides to the active tab and sits flush against the logo plate. */
function TopNav({ role }: { role: Me['role'] }) {
  const { pathname } = useLocation();
  const { t, lang } = useI18n();
  const navRef = useRef<HTMLElement>(null);
  const blockRef = useRef<HTMLSpanElement>(null);
  const placed = useRef(false);

  useLayoutEffect(() => {
    const nav = navRef.current;
    const block = blockRef.current;
    if (!nav || !block) return;
    const place = (animate: boolean) => {
      const active = nav.querySelector<HTMLElement>('a.active');
      if (!active) {
        gsap.set(block, { autoAlpha: 0 });
        return;
      }
      const box = { x: active.offsetLeft, y: active.offsetTop, width: active.offsetWidth, height: active.offsetHeight, autoAlpha: 1 };
      if (animate && !reducedMotion()) gsap.to(block, { ...box, duration: 0.5, overwrite: true });
      else gsap.set(block, { ...box, overwrite: true });
    };
    place(placed.current);
    placed.current = true;
    const onResize = () => place(false);
    window.addEventListener('resize', onResize);
    document.fonts?.ready.then(() => place(false));
    return () => window.removeEventListener('resize', onResize);
  }, [pathname, role, lang]);

  return (
    <nav ref={navRef} className="top-nav" aria-label={t.nav.main}>
      <span ref={blockRef} className="top-block" aria-hidden="true" />
      {TABS[role].map(([to, label, icon]) => (
        <NavLink key={to} to={to} viewTransition>
          <Icon name={icon} />
          <span>{t.nav[label]}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export function Shell() {
  const me = useMe();
  const location = useLocation();
  const client = useQueryClient();
  const { t } = useI18n();
  const logout = useMutation({
    mutationFn: () => api.post('/api/auth/logout'),
    onSuccess: () => {
      client.clear();
      window.location.assign('/login');
    },
  });

  if (me.isPending) return <PageLoader />;
  if (me.isError) return <main className="page-error"><ErrorAlert error={me.error} /></main>;
  if (!me.data) return <Navigate to="/login" replace state={{ from: location.pathname }} />;

  // The exam room is the whole screen: no app bar, so nothing else competes during the test.
  if (location.pathname.startsWith('/exam/')) return <Outlet />;
  const { displayName, role } = me.data;
  return (
    <div className="app">
      <a className="skip" href="#content">{t.common.skip}</a>
      <header className="topbar">
        <Brand to={homeFor(role)} />
        <TopNav role={role} />
        <div className="top-tools">
          <LangSwitch />
          <span className="top-user">
            <span className="user">{displayName}</span>
            <span className="top-role">{t.roles[role]}</span>
          </span>
          {role !== 'admin' && (
            <NavLink className="top-link" to="/settings" viewTransition aria-label={t.nav.settings}>
              <Icon name="settings" />
              <span>{t.nav.settings}</span>
            </NavLink>
          )}
          <button className="top-link" onClick={() => logout.mutate()} disabled={logout.isPending} aria-label={t.nav.logout}>
            <Icon name="out" />
            <span>{t.nav.logout}</span>
          </button>
        </div>
      </header>
      <main id="content" className="page"><Outlet /></main>
    </div>
  );
}

export function RoleGate({ roles }: { roles: Me['role'][] }) {
  const me = useMe();
  if (!me.data) return null;
  if (!roles.includes(me.data.role)) return <Navigate to={homeFor(me.data.role)} replace />;
  return <Outlet />;
}
