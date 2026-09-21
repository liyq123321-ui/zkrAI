import { useCallback, useEffect, useState } from 'react';
import { apiClient } from '../api/client';
import { ROLE_CODES, ROLE_LABELS, type RoleCode } from './session';

interface AdminUser {
  id: string;
  username: string;
  role: RoleCode | string;
  role_name?: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

interface RoleCatalogEntry {
  code: RoleCode | string;
  name: string;
  positioning: string;
  operations: string[];
  operation_names: string[];
  description: string;
  unique: boolean;
  assignable_to_business: boolean;
  default_member: boolean;
}

interface RoleCatalogResponse {
  roles: RoleCatalogEntry[];
  allow_tenant_admin_business: boolean;
}

const overlayStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(15,23,42,0.55)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
};

const panelStyle: React.CSSProperties = {
  width: 'min(94vw, 920px)',
  maxHeight: '88vh',
  overflowY: 'auto',
  background: '#fff',
  borderRadius: 14,
  padding: '22px 24px',
  boxShadow: '0 24px 70px rgba(0,0,0,0.4)',
};

const sectionTitleStyle: React.CSSProperties = {
  margin: '22px 0 10px',
  fontSize: 15,
  color: '#0f172a',
};

export function AdminUsersPage({ onClose }: { onClose: () => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [catalog, setCatalog] = useState<RoleCatalogEntry[]>([]);
  const [allowBusiness, setAllowBusiness] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState<RoleCode>('employee');

  const load = useCallback(async () => {
    setError(null);
    try {
      setUsers(await apiClient.request<AdminUser[]>('/api/admin/users', { method: 'GET' }));
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载用户列表失败。');
    }
    try {
      const result = await apiClient.request<RoleCatalogResponse>('/api/auth/roles', { method: 'GET' });
      setCatalog(result.roles ?? []);
      setAllowBusiness(Boolean(result.allow_tenant_admin_business));
    } catch {
      // The classification table is reference material; a failure here must not
      // block the user list, so fall back to the local labels.
      setCatalog([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function createUser(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    try {
      await apiClient.request('/api/admin/users', {
        method: 'POST',
        body: { username: newUsername, password: newPassword, role: newRole },
      });
      setNewUsername('');
      setNewPassword('');
      setNewRole('employee');
      setNotice(`已创建用户 ${newUsername}`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建用户失败。');
    }
  }

  async function toggleUser(username: string, enable: boolean) {
    setError(null);
    try {
      await apiClient.request(`/api/admin/users/${encodeURIComponent(username)}/${enable ? 'enable' : 'disable'}`, {
        method: 'POST',
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败。');
    }
  }

  async function removeUser(username: string) {
    if (!window.confirm(`确定删除用户 ${username}？此操作不可恢复。`)) return;
    setError(null);
    try {
      await apiClient.request(`/api/admin/users/${encodeURIComponent(username)}`, { method: 'DELETE' });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败。');
    }
  }

  async function resetPassword(username: string) {
    const next = window.prompt(`为 ${username} 设置新密码（至少 8 位）：`);
    if (!next) return;
    setError(null);
    try {
      await apiClient.request(`/api/admin/users/${encodeURIComponent(username)}/reset-password`, {
        method: 'POST',
        body: { new_password: next },
      });
      setNotice(`已重置 ${username} 的密码`);
    } catch (err) {
      setError(err instanceof Error ? err.message : '重置失败。');
    }
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={panelStyle} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0, fontSize: 18, color: '#0f172a' }}>用户管理 · 登录与权限</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'transparent', fontSize: 20, cursor: 'pointer', color: '#64748b' }}>×</button>
        </div>

        {error ? <div style={msgStyle('#dc2626')}>{error}</div> : null}
        {notice ? <div style={msgStyle('#16a34a')}>{notice}</div> : null}

        <h3 style={sectionTitleStyle}>账号列表</h3>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: '#475569' }}>
              <th style={thStyle}>用户名</th>
              <th style={thStyle}>角色</th>
              <th style={thStyle}>状态</th>
              <th style={thStyle}>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} style={{ borderTop: '1px solid #e2e8f0' }}>
                <td style={tdStyle}>{u.username}</td>
                <td style={tdStyle}>
                  <span style={roleNameStyle}>{u.role_name ?? ROLE_LABELS[u.role as RoleCode] ?? u.role}</span>
                  <code style={roleCodeStyle}>{u.role}</code>
                </td>
                <td style={tdStyle}>
                  <span style={{ color: u.is_active ? '#16a34a' : '#dc2626' }}>{u.is_active ? '启用' : '禁用'}</span>
                </td>
                <td style={tdStyle}>
                  <button style={linkBtn} onClick={() => toggleUser(u.username, !u.is_active)}>
                    {u.is_active ? '禁用' : '启用'}
                  </button>
                  <button style={linkBtn} onClick={() => resetPassword(u.username)}>重置密码</button>
                  <button style={{ ...linkBtn, color: '#dc2626' }} onClick={() => removeUser(u.username)}>删除</button>
                </td>
              </tr>
            ))}
            {users.length === 0 ? (
              <tr><td colSpan={4} style={{ ...tdStyle, color: '#94a3b8' }}>暂无用户</td></tr>
            ) : null}
          </tbody>
        </table>

        <h3 style={sectionTitleStyle}>新增账号</h3>
        <form onSubmit={createUser} style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input style={fieldStyle} placeholder="用户名" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} required />
          <input style={fieldStyle} type="password" placeholder="初始密码（至少 8 位）" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required />
          <select style={fieldStyle} value={newRole} onChange={(e) => setNewRole(e.target.value as RoleCode)} aria-label="角色">
            {ROLE_CODES.map((code) => (
              <option key={code} value={code}>{ROLE_LABELS[code]}</option>
            ))}
          </select>
          <button type="submit" style={primaryBtn}>创建用户</button>
          <span style={{ fontSize: 12, color: '#94a3b8' }}>默认角色为一般员工</span>
        </form>

        <h3 style={sectionTitleStyle}>角色分类</h3>
        <p style={{ margin: '0 0 10px', fontSize: 12, color: '#64748b' }}>
          角色决定登录后默认可用的操作点。项目经理可复制、改名、改勾选。
          {allowBusiness ? '（已配置允许租户管理员兼任业务角色）' : null}
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ textAlign: 'left', color: '#475569' }}>
              <th style={thStyle}>角色</th>
              <th style={thStyle}>定位</th>
              <th style={thStyle}>默认操作点</th>
              <th style={thStyle}>说明</th>
            </tr>
          </thead>
          <tbody>
            {(catalog.length > 0 ? catalog : FALLBACK_CATALOG).map((role) => (
              <tr key={role.code} style={{ borderTop: '1px solid #e2e8f0' }}>
                <td style={{ ...tdStyle, whiteSpace: 'nowrap', verticalAlign: 'top' }}>
                  <span style={roleNameStyle}>{role.name}</span>
                  <code style={roleCodeStyle}>{role.code}</code>
                  {role.unique ? <div style={badgeStyle}>默认唯一</div> : null}
                  {role.default_member ? <div style={badgeStyle}>默认成员角色</div> : null}
                </td>
                <td style={{ ...tdStyle, verticalAlign: 'top' }}>{role.positioning}</td>
                <td style={{ ...tdStyle, verticalAlign: 'top', color: '#475569' }}>
                  {(role.operation_names ?? []).join('、')}
                </td>
                <td style={{ ...tdStyle, verticalAlign: 'top', color: '#64748b' }}>{role.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function msgStyle(color: string): React.CSSProperties {
  return { margin: '10px 0', padding: '8px 12px', borderRadius: 8, background: `${color}14`, color, fontSize: 13 };
}
const thStyle: React.CSSProperties = { padding: '8px 10px', fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.4 };
const tdStyle: React.CSSProperties = { padding: '10px', color: '#1e293b' };
const fieldStyle: React.CSSProperties = {
  padding: '8px 10px',
  borderRadius: 8,
  border: '1px solid #cbd5e1',
  fontSize: 14,
  minWidth: 140,
};
const primaryBtn: React.CSSProperties = {
  padding: '8px 14px',
  borderRadius: 8,
  border: 'none',
  background: '#2563eb',
  color: '#fff',
  fontWeight: 600,
  cursor: 'pointer',
};
const linkBtn: React.CSSProperties = {
  border: 'none',
  background: 'transparent',
  color: '#2563eb',
  cursor: 'pointer',
  marginRight: 8,
  fontSize: 13,
};
const roleNameStyle: React.CSSProperties = { color: '#0f172a', fontWeight: 600 };
const roleCodeStyle: React.CSSProperties = {
  marginLeft: 6,
  padding: '1px 5px',
  borderRadius: 4,
  background: '#f1f5f9',
  color: '#64748b',
  fontSize: 11,
};
const badgeStyle: React.CSSProperties = {
  display: 'inline-block',
  marginTop: 4,
  marginRight: 4,
  padding: '1px 6px',
  borderRadius: 999,
  background: '#eef2ff',
  color: '#4338ca',
  fontSize: 11,
};

/**
 * Local copy of the classification, used only if `/api/auth/roles` is
 * unavailable. The backend catalogue stays authoritative.
 */
const FALLBACK_CATALOG: RoleCatalogEntry[] = [
  {
    code: 'tenant_admin', name: '租户管理员', positioning: '租户内系统管理员，不做具体业务操作',
    operations: [], operation_names: ['用户管理', '角色管理', '组织管理', '项目创建', '能力管理', '知识管理', '系统安全'],
    description: '默认唯一，由平台管理员创建；默认不可被授予业务角色（可配置放开）。',
    unique: true, assignable_to_business: false, default_member: false,
  },
  {
    code: 'project_manager', name: '项目经理', positioning: '管理项目事项',
    operations: [], operation_names: ['图与节点 · 创建', '图与节点 · 编辑', '图与节点 · 分派', '审查确认', '看板流转', '检查条目配置', '项目报告导出'],
    description: '可复制、改名、改勾选。',
    unique: false, assignable_to_business: true, default_member: false,
  },
  {
    code: 'employee', name: '一般员工', positioning: '个人空间与任务执行',
    operations: [], operation_names: ['个人文档', '技能', '分身管理', '消息待办', '执行与提交审查'],
    description: '默认成员角色。',
    unique: false, assignable_to_business: true, default_member: true,
  },
  {
    code: 'expert', name: '专家', positioning: '协助审查',
    operations: [], operation_names: ['审查', '评审会', '邀请评审'],
    description: '受邀进入项目。',
    unique: false, assignable_to_business: true, default_member: false,
  },
  {
    code: 'viewer', name: '只读', positioning: '观察与审计',
    operations: [], operation_names: ['查看类操作点'],
    description: '最小权限。',
    unique: false, assignable_to_business: true, default_member: false,
  },
];
