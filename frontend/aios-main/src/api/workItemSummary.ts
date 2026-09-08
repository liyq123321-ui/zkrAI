export function validWorkItemSummary(value: unknown): string | null {
  if (typeof value !== 'string' || value.length === 0 || value !== value.trim()) return null;
  if (/[\r\n\u2028\u2029]/u.test(value) || Array.from(value).length > 20) return null;
  return value;
}
