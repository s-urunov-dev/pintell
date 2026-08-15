import { Route, Routes } from 'react-router-dom';

import Layout from './components/Layout';
import AwardsPage from './pages/AwardsPage';
import ChatPage from './pages/ChatPage';
import CompaniesPage from './pages/CompaniesPage';
import CompanyDetailPage from './pages/CompanyDetailPage';
import ComplianceCheckPage from './pages/ComplianceCheckPage';
import ExpertsPage from './pages/ExpertsPage';
import SearchPage from './pages/SearchPage';
import TeamLeadDetailPage from './pages/TeamLeadDetailPage';
import NotFoundPage from './pages/NotFoundPage';
import TenderDetailPage from './pages/TenderDetailPage';
import TenderListPage from './pages/TenderListPage';
import SignInPage from './pages/SignInPage';
import VendorProfilePage from './pages/VendorProfilePage';

/**
 * The public, anonymous tender browser.
 *
 * The operator console is a separate deployable (`admin-frontend/`) with its
 * own bundle, container and origin — no console code ships here.
 */
export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<TenderListPage />} />
        <Route path="/tenders/:noticeId" element={<TenderDetailPage />} />
        {/* The eligibility check is its own route rather than a panel on the
            tender page: it is a request the vendor makes with their own data,
            and it must be linkable and re-runnable without reloading the
            notice around it. */}
        <Route path="/tenders/:noticeId/compliance" element={<ComplianceCheckPage />} />
        <Route path="/sign-in" element={<SignInPage />} />
        <Route path="/profile" element={<VendorProfilePage />} />
        {/* The directory a verdict sends a vendor to, and one they also
            reach on its own — every filter is in the URL so a shortlist is a
            link somebody can send to a colleague. */}
        <Route path="/experts" element={<ExpertsPage />} />
        {/* Semantic retrieval over the mirror, on its own route rather than
            folded into the tender list: the list filters rows by their
            columns, this reads inside notice bodies and inside the mirrored
            bidding documents, and merging the two would put results with no
            row behind them into a list of rows. */}
        <Route path="/search" element={<SearchPage />} />
        {/* The chat, with the room a kept conversation needs: the thread, the
            sidebar of earlier ones, and answers that run to a dozen claims.
            The floating widget stays for the question asked about the notice
            on screen, and hands its thread over to this route. */}
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/awards" element={<AwardsPage />} />
        <Route path="/companies" element={<CompaniesPage />} />
        <Route path="/companies/:name" element={<CompanyDetailPage />} />
        <Route path="/team-leads/:leadId" element={<TeamLeadDetailPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </Layout>
  );
}
