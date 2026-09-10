import { useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowDown, CheckCircle2, Route } from 'lucide-react';
import type { LifecycleModel, LifecycleRouteDecisionDto } from './dto';

type RouteOption = LifecycleRouteDecisionDto['options'][number];

type RoutePreviewState = {
  option: RouteOption;
  left: number;
  top: number;
};

function EnlargedRoutePreview({ preview }: { preview: RoutePreviewState }) {
  return createPortal(
    <aside className="ff-route-enlarged-preview" style={{ left: preview.left, top: preview.top }} aria-hidden="true">
      <header>
        <span>{preview.option.rank === 'recommended' ? '推荐路线' : '备选路线'}</span>
        <strong>{preview.option.name}</strong>
        <p>{preview.option.reason}</p>
      </header>
      <ol>
        {preview.option.stages.map((stage, index) => <li key={stage.id}>
          <span>{String(stage.number ?? index + 1).padStart(2, '0')}</span>
          <div><strong>{stage.name}</strong><p>{stage.objective}</p><small>{stage.schedule}</small></div>
          {index < preview.option.stages.length - 1 && <i aria-hidden="true" />}
        </li>)}
      </ol>
    </aside>,
    document.body,
  );
}

export function LifecycleRouteChooser({
  decision,
  busy,
  onSelect,
}: {
  decision: LifecycleRouteDecisionDto;
  busy: boolean;
  onSelect: (model: LifecycleModel) => void;
}) {
  const [preview, setPreview] = useState<RoutePreviewState | null>(null);
  const showPreview = (option: RouteOption, element: HTMLElement) => {
    const rect = element.getBoundingClientRect();
    const width = 390;
    const height = Math.min(620, 150 + option.stages.length * 78);
    setPreview({
      option,
      left: Math.min(rect.right + 14, window.innerWidth - width - 16),
      top: Math.max(16, Math.min(rect.top, window.innerHeight - height - 16)),
    });
  };
  return (
    <section className="ff-route-choice" aria-label="SDLC 顶层路线选择">
      <header>
        <span><Route aria-hidden="true" /></span>
        <div>
          <strong>{decision.selected_model ? 'SDLC 路线已确定' : '请选择顶层路线'}</strong>
          <p>{decision.assessment.summary}</p>
        </div>
      </header>
      <div className="ff-route-options">
        {decision.options.map((option) => {
          const selected = decision.selected_model === option.model;
          return (
            <article
              key={option.model}
              className={`ff-route-option ${selected ? 'is-selected' : ''}`}
              onMouseEnter={(event) => showPreview(option, event.currentTarget)}
              onMouseLeave={() => setPreview(null)}
              onFocus={(event) => showPreview(option, event.currentTarget)}
              onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setPreview(null);
              }}
            >
              <div className="ff-route-option-heading">
                <span>{option.rank === 'recommended' ? '推荐方案' : '次选方案'}</span>
                {selected && <CheckCircle2 aria-label="已选择" />}
              </div>
              <h3>{option.name}</h3>
              <p>{option.reason}</p>
              <ol className="ff-route-mini-flow">
                {option.stages.map((stage, index) => (
                  <li key={`${option.model}:${stage.id}`} title={`${stage.objective} · ${stage.schedule}`}>
                    <span>{stage.name}</span>
                    {index < option.stages.length - 1 && <ArrowDown aria-hidden="true" />}
                  </li>
                ))}
              </ol>
              {!decision.selected_model && (
                <button type="button" disabled={busy} onClick={() => onSelect(option.model)}>
                  选择此路线
                </button>
              )}
            </article>
          );
        })}
      </div>
      {!decision.selected_model && <p className="ff-route-choice-note">确认后才会生成 PRD，并在审核通过后按该路线展开完整阶段规划。</p>}
      {preview && <EnlargedRoutePreview preview={preview} />}
    </section>
  );
}
