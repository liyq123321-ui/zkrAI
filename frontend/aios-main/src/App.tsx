import { lazy, Suspense, useState } from 'react';
import { ApiWorkspace } from './api/ApiWorkspace';
import { WorkspacePortal } from './api/WorkspacePortal';
import { LoginPage } from './auth/LoginPage';
import { AdminUsersPage } from './auth/AdminUsersPage';
import { isAdmin, isAuthenticated } from './auth/session';

const PRDEditor = lazy(() => import('./api/prd-editor/PRDEditor').then(module => ({ default: module.PRDEditor })));

export default function App() {
  const [authed, setAuthed] = useState(isAuthenticated());
  const [showAdmin, setShowAdmin] = useState(false);

  if (!authed) {
    return <LoginPage onAuthenticated={() => setAuthed(true)} />;
  }

  const editor = window.location.pathname.match(/^\/prd-editor\/([^/]+)\/?$/);
  if (editor) {
    return (
      <Suspense fallback={<div className="prd-loading">正在加载 Univer 编辑器…</div>}>
        <PRDEditor sessionId={decodeURIComponent(editor[1])} />
      </Suspense>
    );
  }
  if (window.location.pathname.replace(/\/+$/, '') === '/workspace') {
    return <WorkspacePortal />;
  }
  return (
    <>
      {isAdmin() ? (
        <button onClick={() => setShowAdmin(true)} style={adminBtnStyle}>
          用户管理
        </button>
      ) : null}
      {showAdmin ? <AdminUsersPage onClose={() => setShowAdmin(false)} /> : null}
      <ApiWorkspace />
    </>
  );
}

const adminBtnStyle: React.CSSProperties = {
  position: 'fixed',
  top: 16,
  right: 16,
  zIndex: 900,
  padding: '8px 14px',
  borderRadius: 8,
  border: 'none',
  background: '#0f172a',
  color: '#fff',
  fontWeight: 600,
  cursor: 'pointer',
};
