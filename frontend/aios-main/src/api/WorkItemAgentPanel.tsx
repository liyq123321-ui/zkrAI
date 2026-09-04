import { useId, useRef } from 'react';
import { Bot, MessageSquare, PencilLine, Send, ShieldCheck, Square } from 'lucide-react';

export function WorkItemAgentPanel({ draft, onDraftChange }: {
  draft: string;
  onDraftChange?: (value: string) => void;
}) {
  const inputId = useId();
  const input = useRef<HTMLTextAreaElement>(null);

  function prepareRequirementChange() {
    if (!draft.trim()) onDraftChange?.('我想修改这个任务的需求：\n');
    input.current?.focus();
  }

  return (
    <section className="ff-task-agent" aria-label="子 Agent 对话">
      <header className="ff-task-agent-header">
        <div className="ff-task-agent-heading">
          <span className="ff-task-agent-icon"><MessageSquare aria-hidden="true" /></span>
          <div><h3>子 Agent 对话</h3><p>当前任务的专属沟通空间</p></div>
        </div>
        <span className="ff-task-agent-status"><i />待接入</span>
      </header>
      <div className="ff-task-agent-priority"><ShieldCheck aria-hidden="true" /><span>人工指令优先</span><p>接入后，可在这里调整需求与执行方向。</p></div>
      <div className="ff-task-agent-empty">
        <span><Bot aria-hidden="true" /></span>
        <h4>与负责这个任务的 Agent 对话</h4>
        <p>补充需求、讨论实现方案，或在需要时中断任务。</p>
      </div>
      <div className="ff-task-agent-composer">
        <div className="ff-task-agent-shortcuts">
          <button type="button" onClick={prepareRequirementChange} disabled={!onDraftChange}><PencilLine aria-hidden="true" />修改需求</button>
          <button type="button" disabled title="子 Agent 尚未接入，暂不能中断任务"><Square aria-hidden="true" />中断任务</button>
        </div>
        <label className="sr-only" htmlFor={inputId}>给子 Agent 的指令</label>
        <textarea
          id={inputId}
          ref={input}
          value={draft}
          onChange={(event) => onDraftChange?.(event.target.value)}
          readOnly={!onDraftChange}
          placeholder="写下你希望调整的需求、补充说明或执行指令…"
          aria-describedby={`${inputId}-notice`}
        />
        <div className="ff-task-agent-footer">
          <p id={`${inputId}-notice`}>子 Agent 尚未接入，草稿仅保留本页，不会发送或执行。</p>
          <button type="button" className="ff-task-agent-send" disabled><Send aria-hidden="true" />发送指令</button>
        </div>
      </div>
    </section>
  );
}
