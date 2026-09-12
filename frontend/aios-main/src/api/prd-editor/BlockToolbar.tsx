export function BlockToolbar({ dirty, saving, active, hasContent, onSave, onRun, onHistory }: {
  dirty: boolean; saving: boolean; active: boolean; hasContent: boolean; onSave: () => void;
  onRun: (kind: 'generate' | 'revise' | 'review') => void; onHistory: () => void;
}) {
  return <div className="prd-toolbar">
    <button className="prd-primary" disabled={active || saving} onClick={() => onRun('generate')}>{hasContent ? '重新生成' : '生成当前块'}</button>
    <button disabled={active || saving} onClick={() => onRun('revise')}>AI 修改</button>
    <button disabled={active || saving || !hasContent} onClick={() => onRun('review')}>审核当前块</button>
    <button disabled={!dirty || saving} onClick={onSave}>{saving ? '保存中…' : '保存'}</button>
    <button onClick={onHistory}>历史版本</button>
  </div>;
}
