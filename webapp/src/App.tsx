import { ActionMenu, ActionList, Avatar, NavList } from "@primer/react";
import {
  GearIcon,
  GitBranchIcon,
  HomeIcon,
  RepoIcon,
  RocketIcon,
  ShieldCheckIcon,
  SignOutIcon,
  UnlockIcon,
} from "@primer/octicons-react";
import { Link, Outlet, Route, Routes, useLocation } from "react-router";
import { useAuth } from "./auth/AuthProvider";
import { LoginPage } from "./auth/LoginPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { RepoListPage } from "./features/repos/RepoListPage";
import { AddRepoWizard } from "./features/repos/AddRepoWizard";
import { JobDetailPage, JobsPage } from "./features/jobs/JobsPage";
import { JobToaster } from "./features/jobs/JobToaster";
import { RepoLayout } from "./features/explore/RepoLayout";
import { OverviewTab } from "./features/explore/OverviewTab";
import { SymbolsTab } from "./features/explore/SymbolsTab";
import { EntrypointsTab } from "./features/explore/EntrypointsTab";
import { CallGraphTab } from "./features/explore/graph/CallGraphTab";
import { ReachabilityTab } from "./features/explore/reachability/ReachabilityTab";
import { SecurityTab } from "./features/security/SecurityTab";
import { SettingsPage } from "./features/settings/SettingsPage";
import { InstallationPage, SentinelPage } from "./features/sentinel/SentinelPage";
import { EmptyState } from "./components/EmptyState";

function Shell() {
  const { me, logout } = useAuth();
  const location = useLocation();
  const here = (prefix: string) =>
    prefix === "/" ? location.pathname === "/" : location.pathname.startsWith(prefix);

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          <GitBranchIcon />
          entrygraph
          {me?.auth_disabled && (
            <span className="muted" title="authentication disabled (local dev)">
              <UnlockIcon size={14} />
            </span>
          )}
        </div>
        <NavList>
          <NavList.Item as={Link} to="/" aria-current={here("/") ? "page" : undefined}>
            <NavList.LeadingVisual>
              <HomeIcon />
            </NavList.LeadingVisual>
            Dashboard
          </NavList.Item>
          <NavList.Item as={Link} to="/repos" aria-current={here("/repos") ? "page" : undefined}>
            <NavList.LeadingVisual>
              <RepoIcon />
            </NavList.LeadingVisual>
            Repositories
          </NavList.Item>
          <NavList.Item as={Link} to="/jobs" aria-current={here("/jobs") ? "page" : undefined}>
            <NavList.LeadingVisual>
              <RocketIcon />
            </NavList.LeadingVisual>
            Jobs
          </NavList.Item>
          {me?.sentinel_enabled && (
            <NavList.Item
              as={Link}
              to="/sentinel"
              aria-current={here("/sentinel") ? "page" : undefined}
            >
              <NavList.LeadingVisual>
                <ShieldCheckIcon />
              </NavList.LeadingVisual>
              Sentinel
            </NavList.Item>
          )}
          <NavList.Item as={Link} to="/settings" aria-current={here("/settings") ? "page" : undefined}>
            <NavList.LeadingVisual>
              <GearIcon />
            </NavList.LeadingVisual>
            Settings
          </NavList.Item>
        </NavList>
        <div style={{ marginTop: 16, paddingLeft: 8 }}>
          {me && !me.auth_disabled && (
            <ActionMenu>
              <ActionMenu.Button variant="invisible" leadingVisual={() => <Avatar src="" square />}>
                {me.user.name}
              </ActionMenu.Button>
              <ActionMenu.Overlay>
                <ActionList>
                  <ActionList.Item onSelect={() => void logout()}>
                    <ActionList.LeadingVisual>
                      <SignOutIcon />
                    </ActionList.LeadingVisual>
                    Sign out
                  </ActionList.Item>
                </ActionList>
              </ActionMenu.Overlay>
            </ActionMenu>
          )}
        </div>
      </nav>
      <main className="content">
        <Outlet />
      </main>
      <JobToaster />
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Shell />}>
        <Route index element={<DashboardPage />} />
        <Route path="repos" element={<RepoListPage />} />
        <Route path="repos/new" element={<AddRepoWizard />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/:jobId" element={<JobDetailPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="sentinel" element={<SentinelPage />} />
        <Route path="sentinel/installations/:instId" element={<InstallationPage />} />
        <Route path="repos/:repoId" element={<RepoLayout />}>
          <Route index element={<OverviewTab />} />
          <Route path="symbols" element={<SymbolsTab />} />
          <Route path="entrypoints" element={<EntrypointsTab />} />
          <Route path="graph" element={<CallGraphTab />} />
          <Route path="reachability" element={<ReachabilityTab />} />
          <Route path="security" element={<SecurityTab />} />
        </Route>
        <Route
          path="*"
          element={<EmptyState title="Page not found" body="This page doesn't exist." />}
        />
      </Route>
    </Routes>
  );
}
