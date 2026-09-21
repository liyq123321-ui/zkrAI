/** Open the shared workspace in a new tab. Mirrors the home-page "进入工作空间" action. */
export function openSharedWorkspace(): void {
  const configured = import.meta.env.VITE_WORKSPACE_URL?.trim();
  const url = configured || new URL('/workspace', window.location.origin).toString();
  window.open(url, '_blank', 'noopener,noreferrer');
}
