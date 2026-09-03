export function ReviewFindings({ findings }: { findings: Array<Record<string, unknown>> }) {
  return <ul className="space-y-3">{findings.map((finding, index) => <li key={`${finding.code}-${index}`} className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm">
    <div className="mb-1 text-xs text-amber-200">{finding.blocks_progress ? '需要处理' : '建议'} · {String(finding.code || index + 1)}</div>
    <p className="whitespace-pre-wrap text-slate-200">{String(finding.message || '未提供问题说明')}</p>
    {Boolean(finding.suggested_resolution) && <p className="mt-2 whitespace-pre-wrap text-slate-400">建议：{String(finding.suggested_resolution)}</p>}
    {Boolean(finding.spec_path) && <p className="mt-2 break-all text-xs text-slate-500">位置：{String(finding.spec_path)}</p>}
  </li>)}</ul>;
}
