import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

export function RootTaskFilter({ roots, selectedIds, onChange }: {
  roots: Array<{ id: string; title: string }>;
  selectedIds: string[] | null;
  onChange: (ids: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const selected = new Set(selectedIds ?? roots.map((root) => root.id));
  const count = roots.filter((root) => selected.has(root.id)).length;

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, [open]);

  return (
    <div className="ff-root-filter" ref={container} onKeyDown={(event) => {
      if (event.key === 'Escape') { setOpen(false); trigger.current?.focus(); }
    }}>
      <button ref={trigger} type="button" aria-label="筛选主任务" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}>
        <span>主任务：{selectedIds === null ? '全部任务' : `已选 ${count} 项`}</span>
        <ChevronDown aria-hidden="true" />
      </button>
      {open && (
        <div id={panelId} className="ff-root-filter-menu" role="group" aria-label="主任务筛选">
          <div className="ff-root-filter-actions">
            <button type="button" onClick={() => onChange(null)}>全部任务</button>
            <button type="button" onClick={() => onChange([])}>清空选择</button>
          </div>
          <div className="ff-root-filter-options">
            {roots.map((root) => (
              <label key={root.id} title={root.title}>
                <input type="checkbox" checked={selected.has(root.id)} onChange={(event) => {
                  const next = new Set(selected);
                  if (event.target.checked) next.add(root.id); else next.delete(root.id);
                  onChange([...next]);
                }} />
                <span>{root.title}</span>
              </label>
            ))}
            {roots.length === 0 && <p>暂无主任务</p>}
          </div>
          <p>包含所选主任务的全部子 WorkItem</p>
        </div>
      )}
    </div>
  );
}
