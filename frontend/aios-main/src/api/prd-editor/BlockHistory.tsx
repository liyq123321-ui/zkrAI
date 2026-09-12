export function diffLines(before: string, after: string): { kind: 'same' | 'add' | 'remove'; text: string }[] {
  const a = before.split('\n'), b = after.split('\n');
  if (a.length * b.length > 400000) return [...a.map(text => ({ kind: 'remove' as const, text })), ...b.map(text => ({ kind: 'add' as const, text }))];
  const dp = Array.from({ length: a.length + 1 }, () => new Uint32Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const rows: ReturnType<typeof diffLines> = [];
  let i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { rows.push({ kind: 'same', text: a[i++] }); j++; }
    else if (j < b.length && (i === a.length || dp[i][j + 1] > dp[i + 1][j])) rows.push({ kind: 'add', text: b[j++] });
    else rows.push({ kind: 'remove', text: a[i++] });
  }
  return rows;
}
export function BlockDiff({ before, after }: { before: string; after: string }) {
  return <pre className="prd-diff">{diffLines(before, after).map((row, i) => <div className={`is-${row.kind}`} key={i}><span>{row.kind === 'add' ? '+' : row.kind === 'remove' ? '−' : ' '}</span>{row.text || ' '}</div>)}</pre>;
}
export interface Version { id: string; version: number; source: string; snapshot: { content: string }; created_at: string }
export function BlockHistory({ versions, content, onRestore }: { versions: Version[]; content: string; onRestore: (version: number) => void }) {
  return <div className="prd-history">{versions.map(v => <details key={v.id}><summary>v{v.version} · {v.source} · {new Date(v.created_at).toLocaleString()}</summary><BlockDiff before={content} after={v.snapshot.content} /><button onClick={() => onRestore(v.version)}>将 v{v.version} 恢复为新版本</button></details>)}</div>;
}
