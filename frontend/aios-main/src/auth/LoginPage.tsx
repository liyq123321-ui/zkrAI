import { type FormEvent, useState } from 'react';
import { apiClient } from '../api/client';
import { setSession, type SessionUser } from './session';

interface LoginResult {
  access_token: string;
  token_type: string;
  user: SessionUser;
}

const pageStyle: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'linear-gradient(135deg, #0f172a 0%, #1e293b 100%)',
  fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
};

const cardStyle: React.CSSProperties = {
  width: 'min(92vw, 380px)',
  padding: '32px 28px',
  borderRadius: 16,
  background: '#ffffff',
  boxShadow: '0 20px 60px rgba(0,0,0,0.35)',
};

export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await apiClient.request<LoginResult>('/api/auth/login', {
        method: 'POST',
        body: { username, password },
      });
      setSession(result.access_token, result.user);
      onAuthenticated();
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败，请检查用户名和密码。');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={pageStyle}>
      <form style={cardStyle} onSubmit={handleSubmit}>
        <h1 style={{ margin: '0 0 4px', fontSize: 22, color: '#0f172a' }}>登录</h1>
        <p style={{ margin: '0 0 24px', fontSize: 13, color: '#64748b' }}>
          登录后将以你的身份访问系统数据
        </p>

        <label style={labelStyle} htmlFor="login-username">用户名</label>
        <input
          id="login-username"
          style={inputStyle}
          value={username}
          autoComplete="username"
          onChange={(e) => setUsername(e.target.value)}
          placeholder="owner-1"
        />

        <label style={labelStyle} htmlFor="login-password">密码</label>
        <input
          id="login-password"
          style={inputStyle}
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
        />

        {error ? (
          <div style={{ margin: '12px 0 0', fontSize: 13, color: '#dc2626' }}>{error}</div>
        ) : null}

        <button type="submit" disabled={busy} style={buttonStyle(busy)}>
          {busy ? '登录中…' : '登录'}
        </button>
      </form>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: 'block',
  marginTop: 14,
  marginBottom: 6,
  fontSize: 13,
  fontWeight: 600,
  color: '#334155',
};

const inputStyle: React.CSSProperties = {
  width: '100%',
  boxSizing: 'border-box',
  padding: '10px 12px',
  borderRadius: 10,
  border: '1px solid #cbd5e1',
  fontSize: 14,
  outline: 'none',
};

function buttonStyle(busy: boolean): React.CSSProperties {
  return {
    width: '100%',
    marginTop: 22,
    padding: '11px 12px',
    borderRadius: 10,
    border: 'none',
    background: busy ? '#94a3b8' : '#2563eb',
    color: '#fff',
    fontSize: 15,
    fontWeight: 600,
    cursor: busy ? 'default' : 'pointer',
  };
}
