export function BlockToolbar({ dirty, saving, active, hasContent, comparing, compareBusy, onToggleCompare, onSave, onRun, onHistory }: {
  dirty: boolean; saving: boolean; active: boolean; hasContent: boolean;
  comparing: boolean; compareBusy: boolean; onToggleCompare: (next: boolean) => void;
  onSave: () => void; onRun: (kind: 'generate' | 'revise' | 'review') => void; onHistory: () => void;
}) {
  return <div className="prd-toolbar">
    <button className="prd-primary" disabled={active || saving} onClick={() => onRun('generate')}>{hasContent ? '重新生成' : '生成当前块'}</button>
    <label className="prd-compare-toggle" title="在图文阅读里高亮显示与上一版的差异：红色删除线为上一版原有内容，绿色为本次新增">
      <input type="checkbox" checked={comparing} disabled={active || saving || compareBusy} onChange={event => onToggleCompare(event.target.checked)} />
      对比上一版
      {compareBusy && <em>读取中…</em>}
    </label>
    <button disabled={!dirty || saving} onClick={onSave}>{saving ? '保存中…' : '保存'}</button>
    <button onClick={onHistory}>历史版本</button>
  </div>;
}
