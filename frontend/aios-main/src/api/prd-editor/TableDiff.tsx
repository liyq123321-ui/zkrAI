import { useState } from 'react';

function cells(line: string): string[] {
  const text = line.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '');
  const result: string[] = []; let cell = '', code = false, escaped = false;
  for (const char of text) {
    if (escaped) { cell += char; escaped = false; continue; }
    if (char === '\\') { escaped = true; continue; }
    if (char === '`') code = !code;
    if (char === '|' && !code) { result.push(cell.trim()); cell = ''; } else cell += char;
  }
  result.push(cell.trim());
  return result;
}

export function markdownTables(content: string): string[][][] {
  const lines = content.split(/\r?\n/), tables: string[][][] = []; let fence = '';
  for (let i = 0; i < lines.length - 1; i++) {
    const marker = lines[i].trim().match(/^(`{3,}|~{3,})/);
    if (marker) { fence = fence === marker[1][0] ? '' : fence || marker[1][0]; continue; }
    if (fence || !lines[i].includes('|')) continue;
    const divider = cells(lines[i + 1]);
    if (divider.length < 2 || !divider.every(value => /^:?-{3,}:?$/.test(value))) continue;
    const table = [cells(lines[i])]; i += 2;
    while (i < lines.length && lines[i].includes('|') && lines[i].trim()) { table.push(cells(lines[i])); i++; }
    i--; tables.push(table);
  }
  return tables;
}

export function alignTableRows(old: string[][], next: string[][], key: string) {
  const col = old[0]?.indexOf(key) ?? -1, newCol = next[0]?.indexOf(key) ?? -1;
  const unique = (table: string[][], c: number) => c >= 0 && table[0].filter(h => h === key).length === 1 && table.slice(1).every(r => !!r[c]) && new Set(table.slice(1).map(r => r[c])).size === table.length - 1;
  if (!key || !unique(old, col) || !unique(next, newCol)) return null;
  const keys = [...old.slice(1).map(r => r[col]), ...next.slice(1).map(r => r[newCol]).filter(k => !old.slice(1).some(r => r[col] === k))];
  return keys.map(k => ({ key: k, before: old.findIndex((r, i) => i > 0 && r[col] === k), after: next.findIndex((r, i) => i > 0 && r[newCol] === k) }));
}

function TableComparison({ old, next, index }: { old: string[][]; next: string[][]; index: number }) {
  const options = (old[0] ?? []).filter(key => alignTableRows(old, next, key));
  const preferred = options.find(k => /^(id|编号|需求编号|业务编号|名称|功能|功能名称)$/i.test(k)) ?? '';
  const [choice, setChoice] = useState<string | null>(null);
  const key = choice ?? preferred, aligned = alignTableRows(old, next, key);
  const rows = [{ key: 'header', before: old.length ? 0 : -1, after: next.length ? 0 : -1 }, ...(aligned ?? Array.from({ length: Math.max(old.length, next.length) - 1 }, (_, i) => ({ key: String(i + 1), before: old[i + 1] ? i + 1 : -1, after: next[i + 1] ? i + 1 : -1 })))];
  const columns = Math.max(...old.map(row => row.length), ...next.map(row => row.length), 0);
  if (Math.max(old.length, next.length) * columns > 4000) return <p>表格 {index + 1} 较大，请查看下方文本差异。</p>;
  return <details className="prd-table-diff" open><summary>表格 {index + 1} · {aligned ? `按「${key}」对齐业务行` : '按行列位置对照'}</summary>
    <label>行身份 <select value={key} onChange={e => setChoice(e.target.value)}><option value="">按位置（尚未确定业务行身份）</option>{options.map(k => <option key={k}>{k}</option>)}</select></label>
    <div><table><tbody>{rows.map(row => <tr key={row.key}><th>{aligned && row.before > 0 && row.after > 0 && row.before !== row.after ? `行移动 ${row.before} → ${row.after}` : row.before < 0 ? '新增行' : row.after < 0 ? '删除行' : ''}</th>
      {Array.from({ length: columns }, (_, col) => {
        const a = old[row.before]?.[col], b = next[row.after]?.[col], changed = a !== b;
        return <td key={col} className={changed ? a === undefined ? 'is-add' : b === undefined ? 'is-remove' : 'is-change' : ''}>{changed ? <>{a !== undefined && <del>{a || '（空）'}</del>}{b !== undefined && <ins>{b || '（空）'}</ins>}</> : b}</td>;
      })}</tr>)}</tbody></table></div>
  </details>;
}

export function TableDiff({ before, after }: { before: string; after: string }) {
  const oldTables = markdownTables(before), newTables = markdownTables(after);
  return <>{Array.from({ length: Math.max(oldTables.length, newTables.length) }, (_, index) => {
    const old = oldTables[index] ?? [], next = newTables[index] ?? [];
    if (JSON.stringify(old) === JSON.stringify(next)) return null;
    return <TableComparison key={index} old={old} next={next} index={index} />;
  })}</>;
}
