import { CircleUser, ExternalLink, LogOut, Users } from 'lucide-react';
import { getSessionUser, isTenantAdmin, roleLabel } from './session';
import { openSharedWorkspace } from '../api/openSharedWorkspace';

interface SettingsPanelProps {
  onClose: () => void;
  onLogout: () => void;
  onOpenAdmin: () => void;
}

const backdropStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  zIndex: 940,
};

const panelStyle: React.CSSProperties = {
  position: 'fixed',
  bottom: 70,
  left: 60,
  zIndex: 950,
  width: 250,
  background: '#0f172a',
  color: '#e2e8f0',
  borderRadius: 14,
  padding: 14,
  boxShadow: '0 18px 50px rgba(0,0,0,0.5)',
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
};

const accountStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 12,
  padding: '4px 4px 12px',
};

const nameStyle: React.CSSProperties = {
  fontWeight: 600,
  fontSize: 14,
  color: '#f8fafc',
};

const roleStyle: React.CSSProperties = {
  fontSize: 12,
  color: '#94a3b8',
};

const dividerStyle: React.CSSProperties = {
  height: 1,
  background: 'rgba(148,163,184,0.25)',
  margin: '8px 0',
};

const itemStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  width: '100%',
  padding: '10px 8px',
  borderRadius: 8,
  border: 'none',
  background: 'transparent',
  color: '#e2e8f0',
  fontSize: 14,
  textAlign: 'left',
  cursor: 'pointer',
};

const logoutItemStyle: React.CSSProperties = {
  ...itemStyle,
  color: '#f87171',
};

export function SettingsPanel({ onClose, onLogout, onOpenAdmin }: SettingsPanelProps) {
  const user = getSessionUser();
  const canManageUsers = isTenantAdmin();

  return (
    <>
      <div style={backdropStyle} onClick={onClose} />
      <div style={panelStyle} role="menu" aria-label="设置面板">
        <div style={accountStyle}>
          <CircleUser aria-hidden="true" size={38} color="#94a3b8" />
          <div>
            <div style={nameStyle}>{user?.username ?? '未知用户'}</div>
            <div style={roleStyle}>{roleLabel(user)}</div>
          </div>
        </div>

        <div style={dividerStyle} />

        <button
          style={itemStyle}
          role="menuitem"
          onClick={() => {
            onClose();
            openSharedWorkspace();
          }}
        >
          <ExternalLink aria-hidden="true" size={16} />
          进入工作空间
        </button>

        {canManageUsers ? (
          <button
            style={itemStyle}
            role="menuitem"
            onClick={() => {
              onClose();
              onOpenAdmin();
            }}
          >
            <Users aria-hidden="true" size={16} />
            用户管理
          </button>
        ) : null}

        <div style={dividerStyle} />

        <button
          style={logoutItemStyle}
          role="menuitem"
          onClick={() => {
            onClose();
            onLogout();
          }}
        >
          <LogOut aria-hidden="true" size={16} />
          退出登录
        </button>
      </div>
    </>
  );
}
