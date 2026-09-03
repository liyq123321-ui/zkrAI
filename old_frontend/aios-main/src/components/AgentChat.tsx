import React, { useState, useRef, useEffect } from 'react';
import {
  Send,
  Sparkles,
  Bot,
  User,
  ChevronDown,
  ChevronUp,
  Cpu,
  CheckCircle2,
  AlertTriangle,
  PlayCircle,
  PlusCircle,
  Code2,
  RefreshCw,
  Terminal,
  Paperclip,
  Wand2,
  CornerDownLeft,
} from 'lucide-react';
import { AgentChatMessage, AgentInfo, WorkItem } from '../types';
import { AGENTS } from '../utils/mockData';

interface AgentChatProps {
  messages: AgentChatMessage[];
  onSendMessage: (text: string, preferredAgent?: AgentInfo) => void;
  onTriggerQuickAction: (actionKey: string) => void;
  workItems: WorkItem[];
  onSelectWorkItem: (item: WorkItem) => void;
  isProcessing?: boolean;
}

export const AgentChat: React.FC<AgentChatProps> = ({
  messages,
  onSendMessage,
  onTriggerQuickAction,
  workItems,
  onSelectWorkItem,
  isProcessing = false,
}) => {
  const [inputVal, setInputVal] = useState('');
  const [selectedAgent, setSelectedAgent] = useState<AgentInfo>(AGENTS.project_agent);
  const [expandedThoughts, setExpandedThoughts] = useState<Record<string, boolean>>({});
  const chatEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isProcessing]);

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputVal.trim() || isProcessing) return;
    onSendMessage(inputVal, selectedAgent);
    setInputVal('');
  };

  const toggleThought = (msgId: string) => {
    setExpandedThoughts((prev) => ({
      ...prev,
      [msgId]: !prev[msgId],
    }));
  };

  const quickPrompts = [
    {
      label: '⚡ 规划【高可用 API 网关】主工单',
      actionKey: 'create_gateway_epic',
      desc: 'Project Agent 自动生成主工单并在看板就绪',
    },
    {
      label: '🚀 将 WI-101 移入 In Progress',
      actionKey: 'start_wi_101',
      desc: '触发自动分解生成 4 个子任务并关联状态机',
    },
    {
      label: '🛠️ 一键解除 WI-104 瓶颈',
      actionKey: 'unblock_wi_104',
      desc: '补全 Redis 沙箱，恢复 QA 安全审计流转',
    },
    {
      label: '✨ 规划【数据分析看板】主工单',
      actionKey: 'create_analytics_epic',
      desc: '下达图表与指标聚合服务需求',
    },
  ];

  return (
    <aside
      id="agent-chat-container"
      className="w-80 md:w-96 lg:w-[380px] h-full bg-[#131314] border-r border-[#333538] flex flex-col shrink-0 relative overflow-hidden text-[#e3e3e3]"
    >
      {/* Header (AI Studio Style) */}
      <header className="p-3.5 border-b border-[#333538] bg-[#1e1f20] flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-white text-black flex items-center justify-center font-bold">
            <Bot className="w-4 h-4 text-black" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-semibold text-xs text-white">AI Studio Agent Chat</h2>
              <span className="text-[9px] bg-zinc-800 text-zinc-300 px-1.5 py-0.2 rounded border border-[#3c4043] font-mono font-bold">
                ONLINE
              </span>
            </div>
            <p className="text-[10px] text-zinc-400 font-mono">
              Auto-Decomposition & DAG Engine
            </p>
          </div>
        </div>

        {/* Agent Role Switcher */}
        <select
          id="select-active-agent"
          value={selectedAgent.role}
          onChange={(e) => {
            const agentKey = Object.keys(AGENTS).find(
              (k) => AGENTS[k].role === e.target.value
            );
            if (agentKey) setSelectedAgent(AGENTS[agentKey]);
          }}
          className="text-[11px] bg-[#131314] border border-[#333538] text-zinc-200 rounded-md px-2 py-1 focus:outline-none focus:border-zinc-500"
        >
          <option value="project_agent">Project Agent (工单编排)</option>
          <option value="architect_agent">Architect Agent (系统架构)</option>
          <option value="backend_agent">Backend Agent (核心研发)</option>
          <option value="frontend_agent">Frontend Agent (UI研发)</option>
          <option value="qa_agent">QA Agent (验证门禁)</option>
        </select>
      </header>

      {/* Quick Action Suggestion Bar */}
      <div className="px-3 py-2 bg-[#18191b] border-b border-[#333538] overflow-x-auto no-scrollbar flex items-center gap-2">
        <span className="text-[10px] text-zinc-400 font-medium shrink-0 flex items-center gap-1 font-mono">
          <Sparkles className="w-3 h-3 text-zinc-300" />
          快捷:
        </span>
        {quickPrompts.map((p, idx) => (
          <button
            key={idx}
            onClick={() => onTriggerQuickAction(p.actionKey)}
            className="text-[11px] px-2.5 py-1 rounded bg-[#1e1f20] hover:bg-[#282a2c] text-zinc-200 border border-[#333538] hover:border-zinc-500 whitespace-nowrap transition-all flex items-center gap-1 cursor-pointer shrink-0"
            title={p.desc}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((msg) => {
          const isUser = msg.sender.role === 'user';
          const hasThought = msg.thoughtChain && msg.thoughtChain.length > 0;
          const isThoughtExpanded = !!expandedThoughts[msg.id];

          return (
            <div
              key={msg.id}
              className={`flex gap-3 text-xs ${isUser ? 'justify-end' : 'justify-start'}`}
            >
              {/* Agent Avatar */}
              {!isUser && (
                <div
                  className="w-7 h-7 rounded-full flex items-center justify-center text-white text-[10px] font-bold shrink-0 mt-0.5"
                  style={{ backgroundColor: msg.sender.color }}
                >
                  {msg.sender.name.slice(0, 1)}
                </div>
              )}

              <div
                className={`max-w-[85%] flex flex-col space-y-1.5 ${
                  isUser ? 'items-end' : 'items-start'
                }`}
              >
                {/* Meta header */}
                <div className="flex items-center gap-1.5 text-[10px] text-zinc-400 px-1 font-mono">
                  <span className="font-semibold text-zinc-300">{msg.sender.name}</span>
                  <span>•</span>
                  <span>{msg.timestamp}</span>
                </div>

                {/* Message Bubble */}
                <div
                  className={`p-3 rounded-xl leading-relaxed whitespace-pre-wrap ${
                    isUser
                      ? 'bg-white text-black font-medium'
                      : 'bg-[#1e1f20] text-zinc-200 border border-[#333538]'
                  }`}
                >
                  {msg.content}
                </div>

                {/* Agent Thought Chain / Execution Steps Dropdown */}
                {hasThought && (
                  <div className="w-full bg-[#18191b] rounded-lg border border-[#333538] overflow-hidden text-[11px]">
                    <button
                      onClick={() => toggleThought(msg.id)}
                      className="w-full px-2.5 py-1.5 flex items-center justify-between text-zinc-400 hover:text-white bg-[#1e1f20] transition-colors cursor-pointer"
                    >
                      <span className="flex items-center gap-1.5 font-mono text-[10px]">
                        <Cpu className="w-3 h-3 text-zinc-400" />
                        Agent 思考链路 ({msg.thoughtChain?.length} 步骤)
                      </span>
                      {isThoughtExpanded ? (
                        <ChevronUp className="w-3.5 h-3.5" />
                      ) : (
                        <ChevronDown className="w-3.5 h-3.5" />
                      )}
                    </button>

                    {isThoughtExpanded && (
                      <div className="p-2.5 space-y-1.5 bg-[#131314] border-t border-[#333538] font-mono text-[10px] text-zinc-300">
                        {msg.thoughtChain?.map((step, idx) => (
                          <div key={idx} className="flex items-start gap-1.5">
                            <span className="text-zinc-500 font-bold">{idx + 1}.</span>
                            <span className="leading-snug">{step}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* Direct Action WorkItem Jump Pill */}
                {msg.actionPayload?.createdWorkItemId && (
                  <button
                    onClick={() => {
                      const target = workItems.find(
                        (w) => w.id === msg.actionPayload?.createdWorkItemId
                      );
                      if (target) onSelectWorkItem(target);
                    }}
                    className="flex items-center gap-1.5 px-2.5 py-1 bg-[#1e1f20] hover:bg-[#282a2c] text-white border border-[#333538] hover:border-zinc-500 rounded-md text-[11px] font-semibold transition-all cursor-pointer"
                  >
                    <Code2 className="w-3.5 h-3.5 text-zinc-400" />
                    <span>查看关联主工单 #{msg.actionPayload.createdWorkItemId}</span>
                  </button>
                )}
              </div>

              {/* User Avatar */}
              {isUser && (
                <div className="w-7 h-7 rounded-full bg-zinc-200 text-black flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5">
                  TL
                </div>
              )}
            </div>
          );
        })}

        {isProcessing && (
          <div className="flex items-center gap-2.5 text-zinc-400 text-xs py-2 bg-[#1e1f20] p-3 rounded-lg border border-[#333538] animate-pulse font-mono">
            <RefreshCw className="w-3.5 h-3.5 text-white animate-spin" />
            <span>Agent 正在根据架构知识库分解并生成工作项...</span>
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* Input Box */}
      <footer className="p-3 bg-[#1e1f20] border-t border-[#333538]">
        <form onSubmit={handleSend} className="space-y-2">
          <div className="relative bg-[#131314] rounded-lg border border-[#333538] focus-within:border-zinc-500 transition-colors">
            <textarea
              id="input-agent-prompt"
              rows={2}
              value={inputVal}
              onChange={(e) => setInputVal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(e);
                }
              }}
              placeholder={`下达架构指令 (例如: "构建用户会话管理服务", "拆解 OAuth 接入任务")...`}
              className="w-full bg-transparent px-3 py-2 text-xs text-white placeholder-zinc-500 focus:outline-none resize-none"
            />

            <div className="flex items-center justify-between px-2.5 py-1.5 border-t border-[#282a2c] bg-[#18191b] rounded-b-lg">
              <div className="flex items-center gap-1 text-[10px] text-zinc-400 font-mono">
                <span className="w-1.5 h-1.5 rounded-full bg-white"></span>
                <span>Enter 发送 · Shift+Enter 换行</span>
              </div>

              <button
                type="submit"
                disabled={!inputVal.trim() || isProcessing}
                className="px-3 py-1 bg-white hover:bg-zinc-200 disabled:bg-zinc-700 disabled:text-zinc-500 text-black font-semibold rounded-md text-xs transition-colors flex items-center gap-1 cursor-pointer"
              >
                <span>下达任务</span>
                <CornerDownLeft className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </form>
      </footer>
    </aside>
  );
};
