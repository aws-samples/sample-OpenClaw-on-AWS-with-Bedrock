import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ThemeProvider } from './contexts/ThemeContext';
import Layout from './components/Layout';
import PortalLayout from './components/PortalLayout';

import ClawForgeLogo from './components/ClawForgeLogo';

// Pages
import Login from './pages/Login';
import SsoCallback from './pages/SsoCallback';
import Dashboard from './pages/Dashboard';
import DeptTree from './pages/Organization/DeptTree';
import Positions from './pages/Organization/Positions';
import Employees from './pages/Organization/Employees';
import AgentList from './pages/AgentFactory/AgentList';
import AgentDetail from './pages/AgentFactory/AgentDetail';
import SoulEditor from './pages/AgentFactory/SoulEditor';
import ToolsSkills from './pages/ToolsSkills';
import ToolsSkillsDetail from './pages/ToolsSkills/Detail';
import Bindings from './pages/Bindings';
import IMChannels from './pages/IMChannels';
import Monitor from './pages/Monitor/index';
import AuditLog from './pages/AuditLog';
import Usage from './pages/Usage';
import Playground from './pages/Playground';
import Settings from './pages/Settings';
import SecurityCenter from './pages/SecurityCenter';
import Approvals from './pages/Approvals';
import KnowledgeBase from './pages/Knowledge/index';
import Workspace from './pages/Workspace/index';

// Public pages (no auth required)
import TwinChat from './pages/TwinChat';

// Portal pages
import PortalChat from './pages/portal/Chat';
import PortalProfile from './pages/portal/Profile';
import PortalMyUsage from './pages/portal/MyUsage';
import PortalMySkills from './pages/portal/MySkills';
import PortalMyRequests from './pages/portal/MyRequests';
import PortalBindIM from './pages/portal/BindIM';
import PortalMyAgents from './pages/portal/MyAgents';

// Auth flow pages
import ForceChangePassword from './pages/ForceChangePassword';

function AppRoutes() {
  const { user, loading, authMode } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-dark-bg">
        <div className="text-center">
          <ClawForgeLogo size={48} animate="working" />
          <p className="text-sm text-text-muted mt-3">Loading...</p>
        </div>
      </div>
    );
  }

  // Force password change gate — only for local (password) auth
  if (authMode === 'local' && user?.mustChangePassword) {
    return (
      <Routes>
        <Route path="/change-password" element={<ForceChangePassword />} />
        <Route path="/login" element={<Login />} />
        <Route path="/twin/:token" element={<TwinChat />} />
        <Route path="*" element={<Navigate to="/change-password" replace />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to={user.role === 'employee' ? '/portal' : '/dashboard'} replace /> : <Login />} />

      {/* Employee Portal */}
      <Route path="/portal" element={user ? <PortalLayout><PortalChat /></PortalLayout> : <Navigate to="/login" replace />} />
      <Route path="/portal/profile" element={user ? <PortalLayout><PortalProfile /></PortalLayout> : <Navigate to="/login" replace />} />
      <Route path="/portal/usage" element={user ? <PortalLayout><PortalMyUsage /></PortalLayout> : <Navigate to="/login" replace />} />
      <Route path="/portal/skills" element={user ? <PortalLayout><PortalMySkills /></PortalLayout> : <Navigate to="/login" replace />} />
      <Route path="/portal/requests" element={user ? <PortalLayout><PortalMyRequests /></PortalLayout> : <Navigate to="/login" replace />} />
      <Route path="/portal/channels" element={user ? <PortalLayout><PortalBindIM /></PortalLayout> : <Navigate to="/login" replace />} />
      <Route path="/portal/agents" element={user ? <PortalLayout><PortalMyAgents /></PortalLayout> : <Navigate to="/login" replace />} />

      {/* Admin/Manager Console */}
      <Route path="/" element={user ? <Navigate to={user.role === 'employee' ? '/portal' : '/dashboard'} replace /> : <Navigate to="/login" replace />} />
      <Route path="/dashboard" element={user && user.role !== 'employee' ? <Layout><Dashboard /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/org/departments" element={user && user.role !== 'employee' ? <Layout><DeptTree /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/org/positions" element={user && user.role !== 'employee' ? <Layout><Positions /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/org/employees" element={user && user.role !== 'employee' ? <Layout><Employees /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/agents" element={user && user.role !== 'employee' ? <Layout><AgentList /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/agents/:agentId" element={user && user.role !== 'employee' ? <Layout><AgentDetail /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/agents/:agentId/soul" element={user && user.role !== 'employee' ? <Layout><SoulEditor /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/workspace" element={user && user.role !== 'employee' ? <Layout><Workspace /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/skills" element={user && user.role !== 'employee' ? <Layout><ToolsSkills /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/skills/:itemId" element={user && user.role !== 'employee' ? <Layout><ToolsSkillsDetail /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/knowledge" element={user && user.role !== 'employee' ? <Layout><KnowledgeBase /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/bindings" element={user && user.role !== 'employee' ? <Layout><Bindings /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/channels" element={user && user.role !== 'employee' ? <Layout><IMChannels /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/monitor" element={user && user.role !== 'employee' ? <Layout><Monitor /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/audit" element={user && user.role !== 'employee' ? <Layout><AuditLog /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/usage" element={user && user.role !== 'employee' ? <Layout><Usage /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/playground" element={user && user.role !== 'employee' ? <Layout><Playground /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/approvals" element={user && user.role !== 'employee' ? <Layout><Approvals /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/security" element={user && user.role === 'admin' ? <Layout><SecurityCenter /></Layout> : <Navigate to="/login" replace />} />
      <Route path="/settings" element={user && user.role === 'admin' ? <Layout><Settings /></Layout> : <Navigate to="/login" replace />} />

      {/* Catch-all */}
      {/* Public route — no authentication required */}
      <Route path="/twin/:token" element={<TwinChat />} />
      <Route path="/sso/callback" element={<SsoCallback />} />

      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
}
