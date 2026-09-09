import { ArrowDown, CheckCircle2, Route } from 'lucide-react';
import type { LifecycleModel, LifecycleRouteDecisionDto } from './dto';

export function LifecycleRouteChooser({
  decision,
  busy,
  onSelect,
}: {
  decision: LifecycleRouteDecisionDto;
  busy: boolean;
  onSelect: (model: LifecycleModel) => void;
}) {
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
            <article key={option.model} className={`ff-route-option ${selected ? 'is-selected' : ''}`}>
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
    </section>
  );
}
