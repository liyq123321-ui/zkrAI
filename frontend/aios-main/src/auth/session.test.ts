// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest';
import {
  ROLE_CODES,
  ROLE_LABELS,
  clearSession,
  getSessionUser,
  hasOperation,
  isAuthenticated,
  isTenantAdmin,
  normalizeRole,
  roleLabel,
  setSession,
  userOperations,
  type SessionUser,
} from './session';

function user(over: Partial<SessionUser> = {}): SessionUser {
  return { username: 'u1', role: 'employee', is_active: true, ...over };
}

afterEach(() => {
  clearSession();
});

describe('role classification', () => {
  it('exposes the five built-in roles with Chinese labels', () => {
    expect(ROLE_CODES).toEqual([
      'tenant_admin',
      'project_manager',
      'employee',
      'expert',
      'viewer',
    ]);
    expect(ROLE_LABELS.tenant_admin).toBe('租户管理员');
    expect(ROLE_LABELS.project_manager).toBe('项目经理');
    expect(ROLE_LABELS.employee).toBe('一般员工');
    expect(ROLE_LABELS.expert).toBe('专家');
    expect(ROLE_LABELS.viewer).toBe('只读');
  });

  it('maps legacy codes onto canonical roles and defaults to employee', () => {
    expect(normalizeRole('owner')).toBe('project_manager');
    expect(normalizeRole('admin')).toBe('tenant_admin');
    expect(normalizeRole('project_manager')).toBe('project_manager');
    // Unknown values must never grant elevated access.
    expect(normalizeRole('root')).toBe('employee');
    expect(normalizeRole('')).toBe('employee');
    expect(normalizeRole(null)).toBe('employee');
    expect(normalizeRole(undefined)).toBe('employee');
  });

  it('labels a user from role_name, falling back to the local table', () => {
    expect(roleLabel(user({ role: 'tenant_admin' }))).toBe('租户管理员');
    expect(roleLabel(user({ role: 'admin' }))).toBe('租户管理员');
    expect(roleLabel(user({ role: 'owner' }))).toBe('项目经理');
    // A backend-provided label wins (single source of truth).
    expect(roleLabel(user({ role: 'expert', role_name: '领域专家' }))).toBe('领域专家');
    expect(roleLabel(null)).toBe('未登录');
  });
});

describe('session storage', () => {
  it('persists and clears the token + user', () => {
    expect(isAuthenticated()).toBe(false);
    setSession('tok', user({ role: 'tenant_admin' }));
    expect(isAuthenticated()).toBe(true);
    expect(getSessionUser()?.username).toBe('u1');
    clearSession();
    expect(isAuthenticated()).toBe(false);
    expect(getSessionUser()).toBeNull();
  });

  it('survives a corrupt stored payload instead of throwing', () => {
    localStorage.setItem('ff_auth_user', '{not json');
    expect(getSessionUser()).toBeNull();
  });
});

describe('permission helpers', () => {
  it('only treats tenant_admin (and its legacy code) as admin', () => {
    setSession('t', user({ role: 'admin' }));
    expect(isTenantAdmin()).toBe(true);
    setSession('t', user({ role: 'tenant_admin' }));
    expect(isTenantAdmin()).toBe(true);
    setSession('t', user({ role: 'project_manager' }));
    expect(isTenantAdmin()).toBe(false);
    setSession('t', user({ role: 'owner' }));
    expect(isTenantAdmin()).toBe(false);
  });

  it('reads operations from the session and supports the view.* wildcard', () => {
    setSession('t', user({ operations: ['project.graph.assign', 'view.*'] }));
    expect(userOperations()).toEqual(['project.graph.assign', 'view.*']);
    expect(hasOperation('project.graph.assign')).toBe(true);
    expect(hasOperation('view.dashboard')).toBe(true);
    expect(hasOperation('admin.user.manage')).toBe(false);
  });

  it('reports no operations when nothing is stored', () => {
    clearSession();
    expect(userOperations()).toEqual([]);
    expect(hasOperation('view.dashboard')).toBe(false);
  });
});
