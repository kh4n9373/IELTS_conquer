import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Navigate, NavLink, Outlet, useLocation } from 'react-router';
import { api, type Me } from '../lib/api';
import { useI18n } from '../lib/i18n';
import { gsap, reducedMotion } from '../lib/motion';
import type { Copy } from '../locales/vi';
import { homeFor, needsSetup, useMe } from './session';
import { Avatar, Brand, ErrorAlert, Icon, LangSwitch, PageLoader, type IconName } from '../components/ui';

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

/** Account chip that opens a small menu: profile, settings and log out live here, not in the bar. */
function UserMenu({ me, onLogout, loggingOut }: { me: Me; onLogout: () => void; loggingOut: boolean }) {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        boxRef.current?.querySelector<HTMLButtonElement>('.top-user')?.focus();
      }
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const active = pathname.startsWith('/profile') || pathname.startsWith('/settings');
  return (
    <div className="user-menu" ref={boxRef}>
      <button
        type="button"
        className={`top-user${active ? ' active' : ''}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls="user-menu-list"
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar name={me.displayName} url={me.avatarUrl} size={32} />
        <span className="top-user-text">
          <span className="user">{me.displayName}</span>
          <span className="top-role">{t.roles[me.role]}</span>
        </span>
      </button>
      {open && (
        <div className="user-menu-list" id="user-menu-list" role="menu">
          <NavLink role="menuitem" to="/profile" viewTransition>
            <Icon name="users" />
            <span>{t.nav.profile}</span>
          </NavLink>
          {me.role !== 'admin' && (
            <NavLink role="menuitem" to="/settings" viewTransition>
              <Icon name="settings" />
              <span>{t.nav.settings}</span>
            </NavLink>
          )}
          <button role="menuitem" type="button" className="user-menu-out" onClick={onLogout} disabled={loggingOut}>
            <Icon name="out" />
            <span>{t.nav.logout}</span>
          </button>
        </div>
      )}
    </div>
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
  // A new account answers the setup screens once before anything else.
  if (needsSetup(me.data)) return <Navigate to="/setup" replace />;

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
          <UserMenu me={me.data} onLogout={() => logout.mutate()} loggingOut={logout.isPending} />
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
