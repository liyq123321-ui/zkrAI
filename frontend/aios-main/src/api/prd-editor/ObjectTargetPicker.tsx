import { useState } from 'react';
import { markdownTables } from './TableDiff';
import type { Block } from './types';
import type { ObjectTarget } from './discussionContext';

export function uniqueKeyColumns(table: string[][]) {
  return (table[0] ?? []).filter((header, col, headers) => headers.filter(x => x === header).length === 1
    && table.length > 1 && table.slice(1).every(row => !!row[col])
    && new Set(table.slice(1).map(row => row[col])).size === table.length - 1);
}

export function ObjectTargetPicker({ block, onChoose, onClose }: { block: Block; onChoose: (target: ObjectTarget) => void; onClose: () => void }) {
  const tables = markdownTables(block.content);
  const [keys, setKeys] = useState<Record<number, string>>({});
  const [mode, setMode] = useState<'cell' | 'row' | 'column'>('cell');
  const diagrams = [...block.content.matchAll(/^```mermaid[^\n]*\n([\s\S]*?)^```[ \t]*$/gm)];
  return <div className="prd-modal-backdrop"><section className="prd-modal prd-modal-wide" role="dialog" aria-label="选择表格或图中对象" aria-modal="true">
    <header><h3>{block.title} · 选择对象</h3><button onClick={onClose}>关闭</button></header>
    <p>选择唯一的业务定位列后，点击单元格。行排序变化后仍可按业务标识核对。</p>
    <label>修改范围 <select value={mode} onChange={e => setMode(e.target.value as typeof mode)}><option value="cell">单元格</option><option value="row">整行</option><option value="column">整列</option></select></label>
    {tables.map((table, index) => {
      const columns = uniqueKeyColumns(table), key = keys[index] ?? '', keyCol = table[0].indexOf(key);
      return <section key={index} className="prd-table-diff"><h4>表格 {index + 1}</h4>
        {mode !== 'column' && <label>业务定位列 <select aria-label={`表格 ${index + 1} 业务定位列`} value={key} onChange={e => setKeys({ ...keys, [index]: e.target.value })}>
          <option value="">请选择唯一标识列</option>{columns.map(c => <option key={c}>{c}</option>)}</select></label>}
        {!columns.length && mode !== 'column' && <p>当前没有非空且唯一的定位列，请先补充业务编号；暂不猜测行身份。</p>}
        <div><table><thead><tr>{table[0].map((c, i) => <th key={i}>{c}</th>)}</tr></thead><tbody>{table.slice(1).map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c}><button disabled={(mode !== 'column' && keyCol < 0) || table[0].filter(h => h === table[0][c]).length !== 1} onClick={() => onChoose({
          kind: mode, block_id: block.id, block_version: block.version, table_index: index, headers: table[0],
          key_column: mode === 'column' ? undefined : key, key_value: mode === 'column' ? undefined : row[keyCol], column: mode === 'row' ? undefined : table[0][c],
          label: `表格 ${index + 1} · ${mode === 'column' ? '' : `${key}=${row[keyCol]} · `}${mode === 'row' ? '整行' : table[0][c]}`,
        })}>{cell || '（空）'}</button></td>)}</tr>)}</tbody></table></div>
      </section>;
    })}
    {diagrams.map((match, index) => {
      const source = match[1];
      const objects = [...new Set([...source.matchAll(/(?:^|[;\s])([A-Za-z_][\w-]*)\s*(?:\[|\{|\()/gm)].map(m => m[1]).filter(id => !['subgraph', 'style', 'classDef'].includes(id)))];
      return <section key={`d${index}`}><h4>Mermaid 图 {index + 1}</h4><p>节点按图源 ID 引用；复杂图请选整张图并核对图源预览。</p>
        {['', ...objects].map(id => <button key={id} onClick={() => onChoose({ kind: 'mermaid', block_id: block.id, block_version: block.version,
          diagram_index: index, source, object_id: id, label: `Mermaid 图 ${index + 1}${id ? ` · 对象 ${id}` : ' · 整图'}` })}>{id || '选择整张图'}</button>)}
      </section>;
    })}
    {!tables.length && !diagrams.length && <p>当前章节没有可选择的 Markdown 表格或 Mermaid 图。draw.io ER 图可继续通过图 ID 在对话中修改。</p>}
  </section></div>;
}
