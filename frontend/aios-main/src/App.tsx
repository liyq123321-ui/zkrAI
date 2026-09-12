import { ApiWorkspace } from './api/ApiWorkspace';
import { WorkspacePortal } from './api/WorkspacePortal';
import { PRDEditor } from './api/prd-editor/PRDEditor';

export default function App() {
  const editor = window.location.pathname.match(/^\/prd-editor\/([^/]+)\/?$/);
  if (editor) return <PRDEditor sessionId={decodeURIComponent(editor[1])} />;
  if (window.location.pathname.replace(/\/+$/, '') === '/workspace') {
    return <WorkspacePortal />;
  }
  return <ApiWorkspace />;
}
