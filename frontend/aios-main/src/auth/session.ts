export type RoleCode =
  | 'tenant_admin'
  | 'project_manager'
  | 'employee'
  | 'expert'
  | 'viewer';

/** 角色分类的中文名，与后端 `app/auth/roles.py` 的 ROLE_DEFINITIONS 对齐。 */
export const ROLE_LABELS: Record<RoleCode, string> = {
  tenant_admin: '租户管理员',
  project_manager: '项目经理',
  employee: '一般员工',
  expert: '专家',
  viewer: '只读',
};

export const ROLE_CODES: RoleCode[] = [
  'tenant_admin',
  'project_manager',
  'employee',
  'expert',
  'viewer',
];

/**
 * Legacy codes used before the RBAC classification was introduced. Kept so a
 * session stored in localStorage by an older build still labels correctly.
 */
const LEGACY_ROLE_ALIASES: Record<string, RoleCode> = {
  owner: 'project_manager',
  admin: 'tenant_admin',
  'tenant-admin': 'tenant_admin',
  'project-manager': 'project_manager',
  pm: 'project_manager',
  member: 'employee',
  staff: 'employee',
  user: 'employee',
  readonly: 'viewer',
  guest: 'viewer',
};

export interface SessionUser {
  username: string;
  /**
   * Canonical role code. Typed loosely on purpose: the backend normalises
   * legacy values, but a stale localStorage entry must never crash the UI.
   */
  role: RoleCode | string;
  role_name?: string;
  operations?: string[];
  is_active: boolean;
}

const TOKEN_KEY = 'ff_auth_token';
const USER_KEY = 'ff_auth_user';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getSessionUser(): SessionUser | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SessionUser;
  } catch {
    return null;
  }
}

export function setSession(token: string, user: SessionUser): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  for (const key of Object.keys(localStorage)) if ((key.startsWith('ff-prd-recovery:') || key.startsWith('ff-prd-submit:') || key.startsWith('ff-prd-context:'))) localStorage.removeItem(key);
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function isAuthenticated(): boolean {
  return !!getToken();
}

/** Resolve any stored value onto a canonical role code (defaults to 一般员工). */
export function normalizeRole(role?: string | null): RoleCode {
  if (!role) return 'employee';
  const cleaned = role.trim().toLowerCase();
  if ((ROLE_CODES as string[]).includes(cleaned)) return cleaned as RoleCode;
  return LEGACY_ROLE_ALIASES[cleaned] ?? 'employee';
}

/** 角色的中文名，优先用后端下发的 role_name。 */
export function roleLabel(user?: SessionUser | null): string {
  if (!user) return '未登录';
  return user.role_name ?? ROLE_LABELS[normalizeRole(user.role)] ?? '未分配';
}

/** The operation points the current user holds by default. */
export function userOperations(): string[] {
  return getSessionUser()?.operations ?? [];
}

export function hasOperation(operation: string): boolean {
  const operations = userOperations();
  return operations.some(
    (granted) => granted === operation || operation.startsWith(granted.replace(/\*$/, '')),
  );
}

export function isTenantAdmin(): boolean {
  return normalizeRole(getSessionUser()?.role) === 'tenant_admin';
}

/** @deprecated Use {@link isTenantAdmin}. Kept for existing call sites. */
export function isAdmin(): boolean {
  return isTenantAdmin();
}

/** The logged-in user, used as the actor for backend requests. */
export function currentActorId(): string {
  return (
    getSessionUser()?.username ||
    import.meta.env.VITE_LOCAL_ACTOR_HINT?.trim() ||
    'owner-1'
  );
}
