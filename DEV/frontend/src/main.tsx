import '@fontsource-variable/inter-tight/wght.css';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { lazy, StrictMode, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, createRoutesFromElements, Navigate, Outlet, Route, ScrollRestoration } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { AdminPage } from './pages/admin';
import { ForgotPage, LoginPage, ResetPage } from './pages/auth';
import { AssignmentDetailPage } from './pages/assignments/detail';
import { AssignmentsPage } from './pages/assignments/list';
import { ClassDetailPage, ClassesPage } from './pages/classes';
import { JoinPage } from './pages/join';
import { ExamRoomPage } from './pages/exam/room';
import { ResultPage } from './pages/exam/result';
import { ReviewPage } from './pages/exam/review';
import { SettingsPage, StudentHomePage } from './pages/student';
import { DetailsPage, EditorLayout, NewTestPage } from './pages/tests/editor';
import { KeyPage } from './pages/tests/key';
import { TestListPage } from './pages/tests/list';
import { PreviewPage } from './pages/tests/preview';
import { PublishPage } from './pages/tests/publish';
import { StructurePage } from './pages/tests/structure';
import { homeFor, useMe } from './app/session';
import { RoleGate, Shell } from './app/shell';
import { ErrorAlert, PageLoader } from './components/ui';
import { I18nProvider, useI18n } from './lib/i18n';
import { STATIC_PREVIEW } from './lib/preview';
import './styles/global.css';
import './pages/landing/landing.css';
import './pages/landing/authors.css';
import './styles/theme.css';
import './pages/landing/motion.css';
import './pages/tests/tests.css';

const LandingPage = lazy(() => import('./pages/landing/landing'));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
});

function RootRedirect() {
  const me = useMe();
  const { lang } = useI18n();
  if (me.isPending) return <PageLoader bare />;
  if (me.isError) return <main className="page-error"><ErrorAlert error={me.error} /></main>;
  if (me.data) return <Navigate to={homeFor(me.data.role)} replace />;
  return <Suspense fallback={<PageLoader bare />}><LandingPage key={lang} /></Suspense>;
}

function Root() {
  return (
    <>
      <ScrollRestoration />
      <Outlet />
    </>
  );
}

const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<Root />}>
      <Route path="/" element={<RootRedirect />} />
      {!STATIC_PREVIEW && <>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/forgot-password" element={<ForgotPage />} />
      <Route path="/reset-password" element={<ResetPage />} />
      <Route path="/join/:code" element={<JoinPage />} />
      <Route element={<Shell />}>
        <Route element={<RoleGate roles={['admin']} />}>
          <Route path="/admin" element={<AdminPage />} />
        </Route>
        <Route element={<RoleGate roles={['teacher']} />}>
          <Route path="/classes" element={<ClassesPage />} />
          <Route path="/classes/:id" element={<ClassDetailPage />} />
          <Route path="/assignments/:id" element={<AssignmentDetailPage />} />
          <Route path="/tests" element={<TestListPage />} />
          <Route path="/tests/new" element={<NewTestPage />} />
          <Route path="/tests/:id" element={<EditorLayout />}>
            <Route index element={<DetailsPage />} />
            <Route path="structure" element={<StructurePage />} />
            <Route path="key" element={<KeyPage />} />
            <Route path="publish" element={<PublishPage />} />
            <Route path="preview" element={<PreviewPage />} />
          </Route>
        </Route>
        <Route element={<RoleGate roles={['student']} />}>
          <Route path="/home" element={<StudentHomePage />} />
          <Route path="/exam/:attemptId" element={<ExamRoomPage />} />
          <Route path="/results/:attemptId" element={<ResultPage />} />
          <Route path="/results/:attemptId/review" element={<ReviewPage />} />
        </Route>
        <Route element={<RoleGate roles={['student', 'teacher']} />}>
          <Route path="/assignments" element={<AssignmentsPage />} />
        </Route>
        <Route element={<RoleGate roles={['student', 'teacher']} />}>
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Route>
      </>}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Route>,
  ),
  { basename: import.meta.env.BASE_URL },
);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </I18nProvider>
  </StrictMode>,
);
