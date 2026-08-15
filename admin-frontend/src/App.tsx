import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';

import { AuthProvider, useAuth } from './auth/AuthContext';
import ConsoleLayout from './components/ConsoleLayout';
import { useI18n } from './i18n';
import BackfillPage from './pages/BackfillPage';
import CompliancePage from './pages/CompliancePage';
import DashboardPage from './pages/DashboardPage';
import ExplorerPage from './pages/ExplorerPage';
import IndexPage from './pages/IndexPage';
import LoginPage from './pages/LoginPage';
import NoticesPage from './pages/NoticesPage';
import RequirementsPage from './pages/RequirementsPage';
import SyncRunsPage from './pages/SyncRunsPage';
import SystemPage from './pages/SystemPage';

/** Gate for every screen except the login page. */
function RequireStaff() {
  const { user, initialising } = useAuth();
  const location = useLocation();
  const { t } = useI18n();

  if (initialising) {
    return (
      <div className="boot-screen" role="status">
        <span className="spinner" aria-hidden="true" />
        <p>{t('shell.checkingSession')}</p>
      </div>
    );
  }

  if (!user) {
    // Remember where the operator was headed so login can return them there.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireStaff />}>
          <Route element={<ConsoleLayout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/sync-runs" element={<SyncRunsPage />} />
            <Route path="/backfill" element={<BackfillPage />} />
            <Route path="/compliance" element={<CompliancePage />} />
            <Route path="/explorer" element={<ExplorerPage />} />
            <Route path="/requirements" element={<RequirementsPage />} />
            <Route path="/index" element={<IndexPage />} />
            <Route path="/notices" element={<NoticesPage />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
