import { ApiWorkspace } from './api/ApiWorkspace';
import { WorkspacePortal } from './api/WorkspacePortal';

export default function App() {
  if (window.location.pathname.replace(/\/+$/, '') === '/workspace') {
    return <WorkspacePortal />;
  }
  return <ApiWorkspace />;
}
