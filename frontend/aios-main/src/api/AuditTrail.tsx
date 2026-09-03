import { CheckCircle2 } from 'lucide-react';
import type { AuditEventDto, SpecVersionDto } from './dto';
import { auditTitle, displayLabel, displayTime, relatedEventSpecs } from './presentation';
import { ReviewFindings } from './ReviewFindings';

export function AuditTrail({ events, specs }: { events: AuditEventDto[]; specs: SpecVersionDto[] }) {
  return <section className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
    <div className="mb-2 flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-emerald-400" /><h2 className="font-medium">安全审计摘要</h2></div>
    <p className="mb-4 text-xs text-slate-400">点击记录查看执行状态、关联文档和审核结论。</p>
    <ol className="space-y-3">{events.slice().reverse().map((event) => {
      const related = relatedEventSpecs(event, specs);
      return <li key={event.id}><details className="rounded-xl border border-slate-800 bg-slate-950/60 p-3">
        <summary className="cursor-pointer text-sm text-slate-200">
          {auditTitle(event)} <span className="ml-2 text-xs text-cyan-300">查看详情</span>
          <span className="mt-1 block text-xs text-slate-500">{displayTime(event.created_at)} · {event.actor_id || '系统'}{event.payload.status ? ` · ${displayLabel(String(event.payload.status))}` : ''}</span>
        </summary>
        <div className="mt-4 space-y-4 border-t border-slate-800 pt-4">
          <dl className="grid gap-2 text-xs sm:grid-cols-2">{[
            ['事件类型',event.event_type],['执行阶段',event.payload.phase ? displayLabel(String(event.payload.phase)) : null],
            ['开始时间',event.payload.started_at ? displayTime(String(event.payload.started_at)) : null],
            ['完成时间',event.payload.completed_at ? displayTime(String(event.payload.completed_at)) : null],
            ['输入校验值',event.payload.input_hash],['输出校验值',event.payload.output_hash],['错误代码',event.payload.safe_error_code],
          ].filter(([,value]) => value != null).map(([label,value]) => <div key={String(label)}><dt className="text-slate-500">{String(label)}</dt><dd className="break-all text-slate-300">{String(value)}</dd></div>)}</dl>
          {related.map((spec) => {
            const reviews = event.payload.trace_id ? spec.reviews.filter((review) => review.command_id === event.payload.trace_id) : spec.reviews;
            return <div key={spec.id} className="space-y-3">
              <h3 className="text-sm font-medium text-cyan-200">关联 PRD v{spec.revision} · {displayLabel(spec.status)}</h3>
              {reviews.map((review) => <div key={review.id} className="space-y-2"><p className="text-sm">审核结论：{displayLabel(review.verdict)}</p>{review.comments && <p className="whitespace-pre-wrap text-sm text-slate-300">{review.comments}</p>}<ReviewFindings findings={review.findings} /></div>)}
              <details className="rounded-lg border border-slate-800 p-3"><summary className="cursor-pointer text-xs text-cyan-300">查看 PRD v{spec.revision} 内容</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap text-xs leading-6 text-slate-300">{spec.markdown}</pre></details>
            </div>;
          })}
          <details><summary className="cursor-pointer text-xs text-slate-400">事件数据</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-all text-xs leading-5 text-slate-400">{JSON.stringify(event.payload,null,2)}</pre></details>
        </div>
      </details></li>;
    })}</ol>
  </section>;
}
