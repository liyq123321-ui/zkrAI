import React, { useState } from 'react';
import {
  X,
  Clock,
  GitCommit,
  Layers,
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  PlayCircle,
  History,
  FileCode,
  Tag,
  Sparkles,
  ChevronRight,
  ChevronDown,
  Download,
  Copy,
  Check,
  Send,
  Terminal,
  FileText,
  Activity,
  ShieldAlert,
  Search,
  Filter,
  Bot,
  Zap,
  Code2,
  Bug,
  CornerDownLeft,
} from 'lucide-react';
import {
  WorkItem,
  WorkItemStatus,
  AgentInfo,
  ExecutionAuditLog,
  LogLevel,
  AgentChatMessage,
  PRDVersion,
} from '../types';
import { AGENTS } from '../utils/mockData';
import { PRDDocumentModal } from './PRDDocumentModal';

interface WorkItemDetailModalProps {
  item: WorkItem | null;
  allWorkItems: WorkItem[];
  onClose: () => void;
  onStatusChange: (itemId: string, newStatus: WorkItemStatus) => void;
  onSelectWorkItem: (item: WorkItem) => void;
  onUpdateWorkItemLogsAndChat?: (
    itemId: string,
    newLog?: ExecutionAuditLog,
    newChat?: AgentChatMessage
  ) => void;
  onUpdatePRDVersions?: (
    itemId: string,
    updatedVersions: PRDVersion[],
    activeVersion: string,
    auditLog?: ExecutionAuditLog,
    chatMessage?: AgentChatMessage
  ) => void;
}

export const WorkItemDetailModal: React.FC<WorkItemDetailModalProps> = ({
  item,
  allWorkItems,
  onClose,
  onStatusChange,
  onSelectWorkItem,
  onUpdateWorkItemLogsAndChat,
  onUpdatePRDVersions,
}) => {
  const [activeSubTab, setActiveSubTab] = useState<
    'logs' | 'transitions' | 'commits' | 'subtasks'
  >('logs');
  const [showPRDModal, setShowPRDModal] = useState(false);

  // Logs state
  const [logFilter, setLogFilter] = useState<LogLevel | 'ALL'>('ALL');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedLogIds, setExpandedLogIds] = useState<Record<string, boolean>>({
    'log-101-1': true,
    'log-102-1': true,
    'log-102-3': true,
    'log-103-1': true,
    'log-104-2': true,
  });
  const [copiedNotification, setCopiedNotification] = useState<string | null>(null);

  // Agent Chat State within Work Item
  const [chatInput, setChatInput] = useState('');
  const [isAgentResponding, setIsAgentResponding] = useState(false);
  const [localChatMessages, setLocalChatMessages] = useState<AgentChatMessage[]>(
    item?.itemChatMessages || [
      {
        id: `ic-init-${item?.id || '0'}`,
        sender: item?.assignee || AGENTS.project_agent,
        content: `你好！我是负责本工单的 **${item?.assignee.name || 'Agent'}**。您可以在这里直接向我下达指令，如重新执行步骤、调整参数或分析执行日志。`,
        timestamp: '刚刚',
      },
    ]
  );
  const [localLogs, setLocalLogs] = useState<ExecutionAuditLog[]>(
    item?.auditLogs || []
  );

  if (!item) return null;

  const isMainWorkItem = Boolean(
    item.isRootEpic || item.assignee.role === 'project_agent' || (!item.parentId && item.type === 'epic')
  );

  const parentItem = item.parentId
    ? allWorkItems.find((w) => w.id === item.parentId)
    : null;

  const childItems = allWorkItems.filter((w) => item.subItemIds.includes(w.id));

  // Toggle log expansion
  const toggleLogExpand = (logId: string) => {
    setExpandedLogIds((prev) => ({
      ...prev,
      [logId]: !prev[logId],
    }));
  };

  // Export All Logs to JSON file
  const handleExportLogs = () => {
    const exportData = {
      workItemId: item.id,
      title: item.title,
      assignee: item.assignee.name,
      status: item.status,
      exportedAt: new Date().toISOString(),
      totalEntries: localLogs.length,
      logs: localLogs,
    };

    const jsonString = `data:text/json;charset=utf-8,${encodeURIComponent(
      JSON.stringify(exportData, null, 2)
    )}`;
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', jsonString);
    downloadAnchor.setAttribute('download', `${item.id}_audit_logs_${Date.now()}.json`);
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();

    setCopiedNotification('已成功导出所有日志条目 (JSON)');
    setTimeout(() => setCopiedNotification(null), 3000);
  };

  // Copy Logs as formatted Markdown
  const handleCopyLogsMarkdown = () => {
    let md = `# [${item.id}] ${item.title} - 流程审计日志\n`;
    md += `**责任 Agent**: ${item.assignee.name} | **状态**: ${item.status} | **导出时间**: ${new Date().toLocaleString()}\n\n`;
    md += `| 时间 | 级别 | 步骤名称 | 执行耗时 | 摘要 |\n`;
    md += `| :--- | :--- | :--- | :--- | :--- |\n`;

    localLogs.forEach((l) => {
      md += `| ${l.timestamp} | ${l.level} | ${l.stepName} | ${l.durationMs}ms | ${l.summary} |\n`;
    });

    navigator.clipboard.writeText(md);
    setCopiedNotification('已复制 Markdown 审计日志到剪贴板');
    setTimeout(() => setCopiedNotification(null), 3000);
  };

  // Handle User Chat with Assigned Agent
  const handleSendAgentInstruction = (instructionText: string) => {
    if (!instructionText.trim()) return;

    const timeStr = new Date().toTimeString().slice(0, 5);
    const userMsg: AgentChatMessage = {
      id: `ic-user-${Date.now()}`,
      sender: AGENTS.user,
      content: instructionText,
      timestamp: timeStr,
    };

    const updatedChats = [...localChatMessages, userMsg];
    setLocalChatMessages(updatedChats);
    setChatInput('');
    setIsAgentResponding(true);

    // Simulate Agent contextual reasoning & generation of a new real-time audit log
    setTimeout(() => {
      const nowIso = new Date().toISOString().replace('T', ' ').slice(0, 19);
      const isParamAdjust = instructionText.includes('参数') || instructionText.includes('优化') || instructionText.includes('调整');
      const isReRun = instructionText.includes('重跑') || instructionText.includes('重新执行') || instructionText.includes('重新');
      const isDiag = instructionText.includes('诊断') || instructionText.includes('分析') || instructionText.includes('瓶颈');
      const isTest = instructionText.includes('测试') || instructionText.includes('用例') || instructionText.includes('验证');

      let replyContent = '';
      let newLogEntry: ExecutionAuditLog;

      if (isDiag) {
        replyContent = `🔍 **日志与瓶颈诊断完成**：\n已深入扫描最近的步骤日志。目前核心阻碍点在于沙箱与环境依赖隔离，已为您提取调用链路 trace。建议在流转图中触发解除阻塞或切换为 Memory Mock 驱动。`;
        newLogEntry = {
          id: `log-${item.id}-${Date.now()}`,
          timestamp: nowIso,
          level: 'WARN',
          stepName: '指令触发：链路瓶颈与异常调用栈诊断 (Diagnostic Trace)',
          phase: 'runtime',
          executor: item.assignee,
          summary: `响应用户指令："${instructionText.slice(0, 30)}..."，完成深度诊断。`,
          durationMs: 420,
          details: {
            inputParams: { targetItem: item.id, mode: 'deep-inspection' },
            stdout: `[DIAGNOSTIC] Inspecting log stream for ${item.id}...\n[WARN] Connection socket closed prematurely by upstream sandbox.\n[INFO] Recommended action: enable Mock Adapter.`,
            outputResult: { anomalyDetected: true, bottleneckSeverity: 'MEDIUM' },
            exitCode: 0,
          },
        };
      } else if (isParamAdjust) {
        replyContent = `⚙️ **执行参数已更新并重编译**：\n已微调算法签名配置与缓存过期策略（Token TTL = 7200s, RateLimit = 100 req/min）。已重新生成补丁并写入审计流水。`;
        newLogEntry = {
          id: `log-${item.id}-${Date.now()}`,
          timestamp: nowIso,
          level: 'SUCCESS',
          stepName: '指令触发：动态配置微调与重载 (Config Hot-Reload)',
          phase: 'codegen',
          executor: item.assignee,
          summary: `成功应用参数变更并热重载配置。`,
          durationMs: 275,
          details: {
            inputParams: { action: 'tune_parameters', instruction: instructionText },
            stdout: `[INFO] Parsing parameter adjustments...\n[SUCCESS] Hot reloaded 3 configuration variables into runtime container.`,
            outputResult: { reloadStatus: 'success', activeTtlSeconds: 7200 },
            exitCode: 0,
          },
        };
      } else if (isTest) {
        replyContent = `🧪 **自动化测试与断言追加执行完成**：\n已补充 5 组覆盖边界异常（空签名、超长 Payload、SQL 注入探针）的单测用例。测试套件全部通过 (5/5 PASS)！`;
        newLogEntry = {
          id: `log-${item.id}-${Date.now()}`,
          timestamp: nowIso,
          level: 'SUCCESS',
          stepName: '指令触发：边界安全测试用例集执行 (Security Test Assertion)',
          phase: 'testing',
          executor: item.assignee,
          summary: `执行 5 组用户追加的安全渗透用例，全部通过。`,
          durationMs: 640,
          details: {
            inputParams: { addedTestCount: 5, suite: 'dynamic-assertions' },
            stdout: `PASS test/dynamic/boundary.test.ts\n  ✓ should reject empty authorization header (11 ms)\n  ✓ should sanitize malicious payload (18 ms)\n  ✓ should enforce max length limit (22 ms)\n5 passed in 0.64s`,
            assertions: [
              { name: 'Empty header rejection', status: 'passed', durationMs: 11 },
              { name: 'Payload sanitization', status: 'passed', durationMs: 18 },
              { name: 'Length bound guard', status: 'passed', durationMs: 22 },
            ],
            outputResult: { passed: 5, failed: 0 },
            exitCode: 0,
          },
        };
      } else {
        replyContent = `⚡ **指令已执行并写入审计流水**：\n已针对工单 **[${item.id}]** 完成指定步骤的重新执行与验证，日志条目已实时刷新至上方审计面板。`;
        newLogEntry = {
          id: `log-${item.id}-${Date.now()}`,
          timestamp: nowIso,
          level: 'INFO',
          stepName: `指令触发：${instructionText.slice(0, 24)}... (Execution Step)`,
          phase: 'codegen',
          executor: item.assignee,
          summary: `响应 Tech Lead 指令执行特定逻辑。`,
          durationMs: 310,
          details: {
            inputParams: { command: instructionText },
            stdout: `[EXEC] Running target step on container...\n[SUCCESS] Completed in 310ms.`,
            outputResult: { exitStatus: 'OK' },
            exitCode: 0,
          },
        };
      }

      const agentReplyMsg: AgentChatMessage = {
        id: `ic-agent-${Date.now()}`,
        sender: item.assignee,
        content: replyContent,
        timestamp: new Date().toTimeString().slice(0, 5),
        thoughtChain: [
          `接收工单 ${item.id} 指令: "${instructionText.slice(0, 30)}"`,
          `调用 ${item.assignee.name} 领域引擎生成执行流水`,
          `将新生成的审计日志 [${newLogEntry.stepName}] 写入工单持久层`,
        ],
      };

      setLocalChatMessages((prev) => [...prev, agentReplyMsg]);
      setLocalLogs((prev) => [newLogEntry, ...prev]);
      setExpandedLogIds((prev) => ({ ...prev, [newLogEntry.id]: true }));
      setIsAgentResponding(false);

      if (onUpdateWorkItemLogsAndChat) {
        onUpdateWorkItemLogsAndChat(item.id, newLogEntry, agentReplyMsg);
      }
    }, 800);
  };

  // Filtered logs
  const filteredLogs = localLogs.filter((log) => {
    const matchesLevel = logFilter === 'ALL' || log.level === logFilter;
    const matchesSearch =
      searchQuery.trim() === '' ||
      log.stepName.toLowerCase().includes(searchQuery.toLowerCase()) ||
      log.summary.toLowerCase().includes(searchQuery.toLowerCase()) ||
      log.executor.name.toLowerCase().includes(searchQuery.toLowerCase());
    return matchesLevel && matchesSearch;
  });

  // AI Studio Monochrome & Neutral Status Styling
  const statusStyles: Record<
    WorkItemStatus,
    { bg: string; text: string; border: string; label: string }
  > = {
    backlog: {
      bg: 'bg-[#1e1f20]',
      text: 'text-[#9aa0a6]',
      border: 'border-[#333538]',
      label: '待办池 (Backlog)',
    },
    todo: {
      bg: 'bg-[#1e1f20]',
      text: 'text-[#e3e3e3]',
      border: 'border-[#3c4043]',
      label: '待执行 (Todo)',
    },
    in_progress: {
      bg: 'bg-[#282a2c]',
      text: 'text-white font-semibold',
      border: 'border-zinc-500',
      label: '进行中 (In Progress)',
    },
    in_review: {
      bg: 'bg-[#282a2c]',
      text: 'text-[#e3e3e3]',
      border: 'border-zinc-500',
      label: '评审中 (In Review)',
    },
    blocked: {
      bg: 'bg-zinc-900',
      text: 'text-red-300 font-semibold',
      border: 'border-red-800/80',
      label: '已阻塞 (Blocked)',
    },
    done: {
      bg: 'bg-[#1e1f20]',
      text: 'text-zinc-200 font-semibold',
      border: 'border-zinc-600',
      label: '已完成 (Done)',
    },
  };

  const logLevelBadgeStyle: Record<LogLevel, string> = {
    INFO: 'bg-[#1e1f20] text-zinc-300 border-[#3c4043]',
    SUCCESS: 'bg-zinc-800 text-emerald-300 border-zinc-700',
    WARN: 'bg-zinc-800 text-amber-300 border-zinc-700',
    DEBUG: 'bg-[#18191b] text-zinc-400 border-zinc-800',
    ERROR: 'bg-red-950/80 text-red-300 border-red-800',
  };

  return (
    <div
      id="modal-work-item-detail"
      className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-5 bg-black/80 backdrop-blur-md"
    >
      <div className="w-full max-w-5xl bg-[#131314] border border-[#333538] rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[92vh] text-[#e3e3e3]">
        {/* Modal Top Bar (AI Studio Header) */}
        <div className="px-5 py-3 border-b border-[#333538] bg-[#1e1f20] flex items-center justify-between">
          <div className="flex items-center gap-2.5 flex-wrap">
            <span className="font-mono text-xs font-bold text-white px-2.5 py-0.5 bg-[#131314] border border-[#3c4043] rounded-md">
              #{item.id}
            </span>
            <span className="text-[11px] px-2 py-0.5 rounded-md border font-mono font-medium bg-[#18191b] text-zinc-300 border-[#333538]">
              优先级 {item.priority}
            </span>
            <span
              className={`text-[11px] px-2 py-0.5 rounded-md border font-mono ${
                statusStyles[item.status].bg
              } ${statusStyles[item.status].text} ${statusStyles[item.status].border}`}
            >
              {statusStyles[item.status].label.split(' ')[0]}
            </span>
            {item.isRootEpic && (
              <span className="text-[11px] px-2 py-0.5 rounded-md bg-[#18191b] border border-zinc-600 text-white font-medium flex items-center gap-1">
                <Layers className="w-3 h-3 text-zinc-400" />
                Root Epic
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {isMainWorkItem && (
              <button
                id="btn-header-open-prd"
                onClick={() => setShowPRDModal(true)}
                className="px-3 py-1 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-lg transition-all flex items-center gap-1.5 cursor-pointer shadow-sm"
                title="打开项目完整 PRD Markdown 文档并进行逐段批注"
              >
                <FileText className="w-3.5 h-3.5 text-black" />
                <span>PRD 需求规格 (Gitea)</span>
                <span className="text-[10px] font-mono px-1.5 py-0.2 bg-black/10 text-black rounded font-bold">
                  {item.activePrdVersion || (item.prdVersions && item.prdVersions[0]?.version) || 'v1.1'}
                </span>
              </button>
            )}

            <button
              id="btn-close-work-item-detail"
              onClick={onClose}
              className="p-1.5 text-zinc-400 hover:text-white hover:bg-[#282a2c] rounded-md transition-colors cursor-pointer"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Modal Body with 2-Column Split */}
        <div className="flex-1 overflow-y-auto p-5 grid grid-cols-1 lg:grid-cols-12 gap-5">
          {/* Main Content Area (Left 8 cols) */}
          <div className="lg:col-span-8 space-y-4">
            {/* Title & Origin Banner */}
            <div>
              <h1 className="text-base font-semibold text-white leading-snug tracking-tight">
                {item.title}
              </h1>

              {/* Derived from parent work item banner */}
              {parentItem ? (
                <div className="mt-2.5 p-2.5 rounded-lg bg-[#1e1f20] border border-[#333538] flex items-center justify-between">
                  <div className="flex items-center gap-2 text-xs text-zinc-300 truncate mr-2">
                    <Layers className="w-4 h-4 text-zinc-400 shrink-0" />
                    <span className="truncate">
                      产生自上级主工单:{' '}
                      <strong className="text-white font-mono">#{parentItem.id}</strong>{' '}
                      ({parentItem.title})
                    </span>
                  </div>
                  <button
                    onClick={() => onSelectWorkItem(parentItem)}
                    className="text-xs px-2.5 py-1 bg-white hover:bg-zinc-200 text-black font-semibold rounded-md transition-colors flex items-center gap-1 shrink-0 cursor-pointer"
                  >
                    跳转主工单
                    <ChevronRight className="w-3 h-3" />
                  </button>
                </div>
              ) : (
                <div className="mt-1.5 text-xs text-zinc-400 flex items-center gap-1.5 font-mono">
                  <Layers className="w-3.5 h-3.5 text-zinc-500" />
                  <span>根级需求工单 · 由 Agent 对话规格引擎解析生成</span>
                </div>
              )}
            </div>

            {/* Gitea PRD Interactive Entry Banner for Main WorkItems */}
            {isMainWorkItem && (
              <div className="bg-[#1e1f20] p-4 rounded-xl border border-[#333538] flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-sm">
                <div className="flex items-start gap-3">
                  <div className="w-9 h-9 rounded-xl bg-[#131314] border border-[#333538] flex items-center justify-center text-white shrink-0 mt-0.5">
                    <FileText className="w-4 h-4 text-white" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-bold text-white">项目 PRD 规格文档 (Gitea 托管)</span>
                      <span className="text-[10px] font-mono px-2 py-0.2 bg-[#131314] border border-zinc-600 text-zinc-200 rounded font-semibold">
                        {item.activePrdVersion || (item.prdVersions && item.prdVersions[0]?.version) || 'v1.1'} (生效中)
                      </span>
                      <span className="text-[10px] text-zinc-400 font-mono">
                        {item.prdVersions?.length || 2} 个历史版本
                      </span>
                    </div>
                    <p className="text-[11px] text-zinc-400 mt-1 leading-relaxed">
                      调用 Gitea 托管的项目 PRD Markdown 文档。支持逐章节多处批注、一键批量提交与 PM Agent 自动演进新版，并留存全量审计批注支持随时回滚。
                    </p>
                  </div>
                </div>
                <button
                  id="btn-open-prd-banner"
                  onClick={() => setShowPRDModal(true)}
                  className="px-3.5 py-2 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 cursor-pointer shrink-0 shadow-sm"
                >
                  <FileText className="w-3.5 h-3.5 text-black" />
                  <span>打开 PRD 并批注</span>
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {/* Description */}
            <div className="bg-[#1e1f20] p-3.5 rounded-lg border border-[#333538]">
              <h3 className="text-[11px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider font-mono">
                需求与执行规格说明 (Specification)
              </h3>
              <p className="text-xs text-zinc-300 leading-relaxed whitespace-pre-wrap">
                {item.description}
              </p>
            </div>

            {/* Bottleneck Alert Banner if blocked */}
            {item.bottleneckReason && (
              <div className="p-3 bg-red-950/40 border border-red-800/60 rounded-lg flex items-start gap-2.5">
                <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                <div className="text-xs">
                  <div className="font-bold text-red-200">当前任务处于阻塞状态</div>
                  <div className="text-red-300/90 mt-0.5">{item.bottleneckReason}</div>
                </div>
              </div>
            )}

            {/* Sub Tabs Navigation (AI Studio Clean Tab Bar) */}
            <div>
              <div className="flex border-b border-[#333538] gap-4 text-xs font-semibold overflow-x-auto">
                <button
                  id="tab-btn-logs"
                  onClick={() => setActiveSubTab('logs')}
                  className={`pb-2.5 flex items-center gap-1.5 border-b-2 transition-all cursor-pointer whitespace-nowrap ${
                    activeSubTab === 'logs'
                      ? 'text-white border-white'
                      : 'text-zinc-400 border-transparent hover:text-zinc-200'
                  }`}
                >
                  <Terminal className="w-3.5 h-3.5" />
                  流程审计日志 ({localLogs.length})
                </button>

                <button
                  id="tab-btn-transitions"
                  onClick={() => setActiveSubTab('transitions')}
                  className={`pb-2.5 flex items-center gap-1.5 border-b-2 transition-all cursor-pointer whitespace-nowrap ${
                    activeSubTab === 'transitions'
                      ? 'text-white border-white'
                      : 'text-zinc-400 border-transparent hover:text-zinc-200'
                  }`}
                >
                  <History className="w-3.5 h-3.5" />
                  状态流转记录 ({item.transitionHistory.length})
                </button>

                <button
                  id="tab-btn-commits"
                  onClick={() => setActiveSubTab('commits')}
                  className={`pb-2.5 flex items-center gap-1.5 border-b-2 transition-all cursor-pointer whitespace-nowrap ${
                    activeSubTab === 'commits'
                      ? 'text-white border-white'
                      : 'text-zinc-400 border-transparent hover:text-zinc-200'
                  }`}
                >
                  <GitCommit className="w-3.5 h-3.5" />
                  关联提交 ({item.commits.length})
                </button>

                {item.subItemIds.length > 0 && (
                  <button
                    id="tab-btn-subtasks"
                    onClick={() => setActiveSubTab('subtasks')}
                    className={`pb-2.5 flex items-center gap-1.5 border-b-2 transition-all cursor-pointer whitespace-nowrap ${
                      activeSubTab === 'subtasks'
                        ? 'text-white border-white'
                        : 'text-zinc-400 border-transparent hover:text-zinc-200'
                    }`}
                  >
                    <Layers className="w-3.5 h-3.5" />
                    裂变子工单 ({item.subItemIds.length})
                  </button>
                )}
              </div>

              {/* TAB 1: 流程审计日志 (Execution & Audit Logs) */}
              {activeSubTab === 'logs' && (
                <div className="mt-3.5 space-y-3">
                  {/* Top Bar for Logs: Search + Level Filter + Export All Button */}
                  <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-2.5 p-2.5 bg-[#1e1f20] border border-[#333538] rounded-lg">
                    {/* Search & Filter */}
                    <div className="flex items-center gap-2 flex-1">
                      <div className="relative flex-1">
                        <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
                        <input
                          type="text"
                          value={searchQuery}
                          onChange={(e) => setSearchQuery(e.target.value)}
                          placeholder="搜索步骤名称 / 摘要 / 执行者..."
                          className="w-full bg-[#131314] border border-[#333538] rounded-md pl-8 pr-2.5 py-1 text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-zinc-500"
                        />
                      </div>

                      {/* Log Level Filter Select */}
                      <select
                        value={logFilter}
                        onChange={(e) => setLogFilter(e.target.value as LogLevel | 'ALL')}
                        className="bg-[#131314] border border-[#333538] rounded-md px-2 py-1 text-xs text-zinc-300 focus:outline-none focus:border-zinc-500"
                      >
                        <option value="ALL">全部级别 (ALL)</option>
                        <option value="INFO">INFO</option>
                        <option value="SUCCESS">SUCCESS</option>
                        <option value="WARN">WARN</option>
                        <option value="DEBUG">DEBUG</option>
                        <option value="ERROR">ERROR</option>
                      </select>
                    </div>

                    {/* Export Actions */}
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={handleCopyLogsMarkdown}
                        title="复制 Markdown 报告"
                        className="px-2.5 py-1 bg-[#131314] hover:bg-[#282a2c] text-zinc-300 border border-[#333538] rounded-md text-xs font-medium flex items-center gap-1.5 transition-colors cursor-pointer"
                      >
                        <Copy className="w-3.5 h-3.5 text-zinc-400" />
                        <span>复制报告</span>
                      </button>

                      <button
                        id="btn-export-all-logs"
                        onClick={handleExportLogs}
                        title="一键导出所有 JSON 条目"
                        className="px-3 py-1 bg-white hover:bg-zinc-200 text-black rounded-md text-xs font-semibold flex items-center gap-1.5 transition-colors cursor-pointer shadow-sm"
                      >
                        <Download className="w-3.5 h-3.5 text-black" />
                        <span>一键导出所有条目</span>
                      </button>
                    </div>
                  </div>

                  {/* Toast notification if copied/exported */}
                  {copiedNotification && (
                    <div className="p-2 rounded bg-zinc-800 border border-zinc-600 text-xs text-white flex items-center gap-2 animate-fadeIn">
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                      <span>{copiedNotification}</span>
                    </div>
                  )}

                  {/* Logs List Container */}
                  <div className="space-y-2.5">
                    {filteredLogs.length === 0 ? (
                      <div className="p-8 text-center bg-[#1e1f20] border border-[#333538] rounded-lg text-xs text-zinc-400">
                        未匹配到符合条件的流程审计日志条目。
                      </div>
                    ) : (
                      filteredLogs.map((log, index) => {
                        const isExpanded = !!expandedLogIds[log.id];

                        return (
                          <div
                            key={log.id || index}
                            className="bg-[#1e1f20] border border-[#333538] hover:border-zinc-500 rounded-lg overflow-hidden transition-all text-xs"
                          >
                            {/* Log Item Header Summary Row (Clickable) */}
                            <div
                              onClick={() => toggleLogExpand(log.id)}
                              className="p-3 flex items-start justify-between gap-3 cursor-pointer hover:bg-[#282a2c]/60 select-none transition-colors"
                            >
                              <div className="flex items-start gap-2.5 flex-1 min-w-0">
                                {/* Level Badge */}
                                <span
                                  className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold border shrink-0 ${
                                    logLevelBadgeStyle[log.level] ||
                                    logLevelBadgeStyle.INFO
                                  }`}
                                >
                                  {log.level}
                                </span>

                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="font-semibold text-white truncate">
                                      {log.stepName}
                                    </span>
                                    <span className="text-[10px] px-1.5 py-0.2 bg-[#131314] text-zinc-400 border border-[#333538] rounded font-mono">
                                      {log.phase}
                                    </span>
                                    <span className="text-[10px] text-zinc-500 font-mono">
                                      {log.durationMs}ms
                                    </span>
                                  </div>

                                  <div className="text-zinc-300 mt-1 line-clamp-2 leading-relaxed">
                                    {log.summary}
                                  </div>
                                </div>
                              </div>

                              <div className="flex items-center gap-2 shrink-0">
                                <div className="flex items-center gap-1.5 text-[11px] text-zinc-400 font-mono hidden sm:flex">
                                  <div
                                    className="w-2 h-2 rounded-full"
                                    style={{ backgroundColor: log.executor.color }}
                                  ></div>
                                  <span>{log.executor.name.split(' ')[0]}</span>
                                </div>

                                <span className="text-[10px] text-zinc-500 font-mono">
                                  {log.timestamp.slice(-8)}
                                </span>

                                <div className="p-1 text-zinc-500 hover:text-white">
                                  {isExpanded ? (
                                    <ChevronDown className="w-4 h-4" />
                                  ) : (
                                    <ChevronRight className="w-4 h-4" />
                                  )}
                                </div>
                              </div>
                            </div>

                            {/* Log Item Expanded Details View */}
                            {isExpanded && (
                              <div className="border-t border-[#333538] p-3.5 bg-[#131314] space-y-3">
                                {/* Details: Input Parameters */}
                                {log.details.inputParams && (
                                  <div>
                                    <div className="flex items-center justify-between text-[11px] text-zinc-400 font-mono mb-1">
                                      <span className="font-semibold text-zinc-300">
                                        📥 输入与调度参数 (Input Parameters)
                                      </span>
                                    </div>
                                    <pre className="p-2.5 rounded bg-[#18191b] border border-[#282a2c] text-[11px] font-mono text-zinc-300 overflow-x-auto leading-relaxed">
                                      {JSON.stringify(log.details.inputParams, null, 2)}
                                    </pre>
                                  </div>
                                )}

                                {/* Details: Standard Output Logs */}
                                {log.details.stdout && (
                                  <div>
                                    <div className="flex items-center justify-between text-[11px] text-zinc-400 font-mono mb-1">
                                      <span className="font-semibold text-zinc-300">
                                        📟 标准执行输出 (stdout / Console)
                                      </span>
                                      <span className="text-[10px] text-zinc-500">
                                        Exit Code: {log.details.exitCode ?? 0}
                                      </span>
                                    </div>
                                    <pre className="p-2.5 rounded bg-[#0b0c0e] border border-[#282a2c] text-[11px] font-mono text-zinc-200 overflow-x-auto whitespace-pre-wrap leading-relaxed">
                                      {log.details.stdout}
                                    </pre>
                                  </div>
                                )}

                                {/* Details: Stack Trace if Error/Warn */}
                                {log.details.stackTrace && (
                                  <div>
                                    <div className="text-[11px] text-red-400 font-mono font-semibold mb-1">
                                      ⚠️ 异常堆栈追踪 (Stack Trace)
                                    </div>
                                    <pre className="p-2.5 rounded bg-red-950/30 border border-red-900/60 text-[11px] font-mono text-red-200 overflow-x-auto whitespace-pre-wrap leading-relaxed">
                                      {log.details.stackTrace}
                                    </pre>
                                  </div>
                                )}

                                {/* Details: Assertion Checklist if any */}
                                {log.details.assertions &&
                                  log.details.assertions.length > 0 && (
                                    <div>
                                      <div className="text-[11px] text-zinc-300 font-mono font-semibold mb-1">
                                        🛡️ 质量门禁与测试断言 (Assertions & Gates)
                                      </div>
                                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                                        {log.details.assertions.map((ast, aIdx) => (
                                          <div
                                            key={aIdx}
                                            className="p-2 rounded bg-[#18191b] border border-[#282a2c] flex items-center justify-between text-[11px]"
                                          >
                                            <span className="text-zinc-300 truncate">
                                              {ast.name}
                                            </span>
                                            <span
                                              className={`font-mono text-[10px] font-bold px-1.5 py-0.2 rounded ${
                                                ast.status === 'passed'
                                                  ? 'text-emerald-400 bg-emerald-950/60'
                                                  : 'text-red-400 bg-red-950/60'
                                              }`}
                                            >
                                              {ast.status} ({ast.durationMs}ms)
                                            </span>
                                          </div>
                                        ))}
                                      </div>
                                    </div>
                                  )}

                                {/* Details: Output Result Artifacts */}
                                {log.details.outputResult && (
                                  <div>
                                    <div className="text-[11px] text-zinc-400 font-mono mb-1">
                                      📤 执行结果产物 (Output Artifacts)
                                    </div>
                                    <pre className="p-2.5 rounded bg-[#18191b] border border-[#282a2c] text-[11px] font-mono text-zinc-300 overflow-x-auto leading-relaxed">
                                      {JSON.stringify(log.details.outputResult, null, 2)}
                                    </pre>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              )}

              {/* TAB 2: 状态流转记录 (Transition History) */}
              {activeSubTab === 'transitions' && (
                <div className="mt-3.5 space-y-2.5">
                  {item.transitionHistory.length === 0 ? (
                    <div className="text-xs text-zinc-500 py-6 text-center bg-[#1e1f20] rounded-lg border border-[#333538]">
                      暂无状态流转审计记录
                    </div>
                  ) : (
                    item.transitionHistory.map((tr, index) => {
                      const fromInfo = statusStyles[tr.fromStatus];
                      const toInfo = statusStyles[tr.toStatus];

                      return (
                        <div
                          key={tr.id || index}
                          className="p-3 rounded-lg bg-[#1e1f20] border border-[#333538] text-xs flex flex-col gap-2 relative overflow-hidden"
                        >
                          <div className="flex items-center justify-between">
                            {/* Status Shift Badge */}
                            <div className="flex items-center gap-2">
                              <span
                                className={`px-2 py-0.5 rounded text-[11px] font-medium ${fromInfo.bg} ${fromInfo.text} border ${fromInfo.border}`}
                              >
                                {fromInfo.label.split(' ')[0]}
                              </span>
                              <ArrowRight className="w-3.5 h-3.5 text-zinc-500" />
                              <span
                                className={`px-2 py-0.5 rounded text-[11px] font-bold ${toInfo.bg} ${toInfo.text} border ${toInfo.border}`}
                              >
                                {toInfo.label.split(' ')[0]}
                              </span>
                            </div>

                            {/* Timestamp */}
                            <div className="flex items-center gap-1 text-[11px] text-zinc-400 font-mono">
                              <Clock className="w-3 h-3" />
                              {tr.timestamp}
                            </div>
                          </div>

                          {/* Actor Info */}
                          <div className="flex items-center gap-2 text-[11px]">
                            <span className="text-zinc-400">操作主体:</span>
                            <div className="flex items-center gap-1.5 px-1.5 py-0.5 bg-[#131314] border border-[#333538] rounded text-zinc-200">
                              <span
                                className="w-2 h-2 rounded-full"
                                style={{ backgroundColor: tr.actor.color }}
                              ></span>
                              <span className="font-semibold">{tr.actor.name}</span>
                            </div>
                            {tr.durationInPrevState && (
                              <span className="text-zinc-500 ml-auto font-mono text-[10px]">
                                前置停留: {tr.durationInPrevState}
                              </span>
                            )}
                          </div>

                          {/* Reason */}
                          <div className="text-zinc-300 bg-[#131314] p-2.5 rounded border border-[#333538]">
                            <span className="text-white font-medium">流转触发说明: </span>
                            {tr.reason}
                          </div>

                          {tr.notes && (
                            <div className="text-[11px] text-zinc-400 italic">
                              备注: {tr.notes}
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              )}

              {/* TAB 3: Git Commits & Diffs */}
              {activeSubTab === 'commits' && (
                <div className="mt-3.5 space-y-2.5">
                  {item.commits.length === 0 ? (
                    <div className="text-xs text-zinc-400 py-8 text-center bg-[#1e1f20] rounded-lg border border-[#333538]">
                      尚未关联 Git 提交记录。可在下方指令框中让责任 Agent 编写并提交代码。
                    </div>
                  ) : (
                    item.commits.map((commit) => (
                      <div
                        key={commit.id}
                        className="p-3.5 rounded-lg bg-[#1e1f20] border border-[#333538] text-xs space-y-2.5"
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <GitCommit className="w-4 h-4 text-zinc-300" />
                            <span className="font-mono font-bold text-white">
                              {commit.shortHash}
                            </span>
                            <span className="text-zinc-200 font-medium truncate max-w-md">
                              {commit.message}
                            </span>
                          </div>
                          <span className="text-[11px] text-zinc-500 font-mono">
                            {commit.timestamp}
                          </span>
                        </div>

                        <div className="flex items-center gap-3 text-[11px] text-zinc-400 font-mono">
                          <div className="flex items-center gap-1">
                            <span>Author:</span>
                            <span className="text-zinc-200 font-semibold">{commit.author.name}</span>
                          </div>
                          <div>
                            <span className="text-emerald-400">+{commit.insertions}</span>
                            <span className="text-red-400 ml-1.5">-{commit.deletions}</span>
                          </div>
                          <div className="text-zinc-500">{commit.filesChanged} 个文件修改</div>
                        </div>

                        {/* File Diff Snippets */}
                        <div className="space-y-1.5 pt-1">
                          {commit.files.map((file, fIdx) => (
                            <div
                              key={fIdx}
                              className="bg-[#131314] rounded-lg border border-[#333538] overflow-hidden"
                            >
                              <div className="px-2.5 py-1.5 bg-[#18191b] border-b border-[#333538] flex items-center justify-between text-[11px] text-zinc-300 font-mono">
                                <div className="flex items-center gap-1.5">
                                  <FileCode className="w-3.5 h-3.5 text-zinc-400" />
                                  <span>{file.name}</span>
                                </div>
                                <span className="text-[10px] uppercase font-bold px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-200 border border-zinc-700">
                                  {file.status}
                                </span>
                              </div>
                              <pre className="p-2.5 text-[11px] font-mono text-zinc-200 bg-[#0e0e10] overflow-x-auto whitespace-pre leading-relaxed">
                                {file.diffSnippet}
                              </pre>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}

              {/* TAB 4: 已裂变子工单 */}
              {activeSubTab === 'subtasks' && (
                <div className="mt-3.5 space-y-2">
                  {childItems.map((child) => (
                    <div
                      key={child.id}
                      onClick={() => onSelectWorkItem(child)}
                      className="p-3 bg-[#1e1f20] hover:bg-[#282a2c] border border-[#333538] hover:border-zinc-500 rounded-lg transition-all cursor-pointer flex items-center justify-between group"
                    >
                      <div className="flex items-center gap-3">
                        <span className="font-mono text-xs font-bold text-white px-2 py-0.5 bg-[#131314] rounded border border-[#333538]">
                          #{child.id}
                        </span>
                        <div>
                          <div className="text-xs font-semibold text-zinc-100 group-hover:text-white transition-colors">
                            {child.title}
                          </div>
                          <div className="flex items-center gap-2 text-[11px] text-zinc-400 mt-0.5">
                            <span>执行者: {child.assignee.name}</span>
                            <span>•</span>
                            <span>进度: {child.progress}%</span>
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded font-mono ${
                            statusStyles[child.status].bg
                          } ${statusStyles[child.status].text} border ${
                            statusStyles[child.status].border
                          }`}
                        >
                          {statusStyles[child.status].label.split(' ')[0]}
                        </span>
                        <ChevronRight className="w-4 h-4 text-zinc-500 group-hover:text-zinc-200" />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* DEDICATED WORKITEM AGENT INTERACTION CHAT BOX */}
            <div className="mt-6 pt-4 border-t border-[#333538] space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="w-6 h-6 rounded-full bg-white text-black flex items-center justify-center font-bold text-xs">
                    <Bot className="w-3.5 h-3.5" />
                  </div>
                  <div>
                    <span className="text-xs font-bold text-white">
                      责任 Agent 交互对话与指令中枢
                    </span>
                    <span className="text-[11px] text-zinc-400 ml-2 font-mono">
                      @{item.assignee.name}
                    </span>
                  </div>
                </div>
                <span className="text-[10px] text-zinc-400 font-mono px-2 py-0.5 bg-[#1e1f20] border border-[#333538] rounded">
                  工单上下文隔离
                </span>
              </div>

              {/* Chat Message Stream */}
              <div className="max-h-48 overflow-y-auto space-y-2 p-3 bg-[#1e1f20] rounded-lg border border-[#333538]">
                {localChatMessages.map((msg) => {
                  const isUser = msg.sender.role === 'user';

                  return (
                    <div
                      key={msg.id}
                      className={`flex gap-2.5 ${isUser ? 'justify-end' : 'justify-start'}`}
                    >
                      {!isUser && (
                        <div
                          className="w-6 h-6 rounded-full flex items-center justify-center text-white text-[10px] font-bold shrink-0 mt-0.5"
                          style={{ backgroundColor: msg.sender.color }}
                        >
                          {msg.sender.name.slice(0, 1)}
                        </div>
                      )}

                      <div
                        className={`max-w-[85%] rounded-lg p-2.5 text-xs leading-relaxed ${
                          isUser
                            ? 'bg-white text-black font-medium'
                            : 'bg-[#131314] text-zinc-200 border border-[#333538]'
                        }`}
                      >
                        {!isUser && (
                          <div className="text-[10px] text-zinc-400 font-mono mb-1 flex items-center justify-between">
                            <span>{msg.sender.name}</span>
                            <span className="text-zinc-500">{msg.timestamp}</span>
                          </div>
                        )}
                        <div className="whitespace-pre-wrap">{msg.content}</div>
                      </div>

                      {isUser && (
                        <div className="w-6 h-6 rounded-full bg-zinc-300 text-black flex items-center justify-center text-[10px] font-bold shrink-0 mt-0.5">
                          TL
                        </div>
                      )}
                    </div>
                  );
                })}

                {isAgentResponding && (
                  <div className="flex items-center gap-2 text-xs text-zinc-400 font-mono py-1">
                    <div className="w-2 h-2 rounded-full bg-white animate-ping"></div>
                    <span>{item.assignee.name} 正在分析工单上下文并执行指令...</span>
                  </div>
                )}
              </div>

              {/* Quick Instruction Suggestion Chips */}
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[10px] text-zinc-500 font-mono">快捷指令:</span>
                <button
                  onClick={() =>
                    handleSendAgentInstruction('重新执行此步骤并输出 Verbose 诊断日志')
                  }
                  className="px-2 py-0.5 rounded bg-[#1e1f20] hover:bg-[#282a2c] text-[10px] text-zinc-300 border border-[#333538] transition-colors cursor-pointer"
                >
                  ⚡ 重跑并输出详细日志
                </button>
                <button
                  onClick={() =>
                    handleSendAgentInstruction('诊断最近一次执行的瓶颈并提供解除建议')
                  }
                  className="px-2 py-0.5 rounded bg-[#1e1f20] hover:bg-[#282a2c] text-[10px] text-zinc-300 border border-[#333538] transition-colors cursor-pointer"
                >
                  🔍 诊断当前瓶颈异常
                </button>
                <button
                  onClick={() =>
                    handleSendAgentInstruction('优化执行参数 (Token TTL = 7200s) 并重载')
                  }
                  className="px-2 py-0.5 rounded bg-[#1e1f20] hover:bg-[#282a2c] text-[10px] text-zinc-300 border border-[#333538] transition-colors cursor-pointer"
                >
                  ⚙️ 调整执行参数
                </button>
                <button
                  onClick={() =>
                    handleSendAgentInstruction('追加 5 组边界安全单测用例并验证')
                  }
                  className="px-2 py-0.5 rounded bg-[#1e1f20] hover:bg-[#282a2c] text-[10px] text-zinc-300 border border-[#333538] transition-colors cursor-pointer"
                >
                  🧪 补充单测用例
                </button>
              </div>

              {/* Chat Input Bar */}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSendAgentInstruction(chatInput);
                }}
                className="flex items-center gap-2 bg-[#1e1f20] p-1.5 border border-[#333538] rounded-lg focus-within:border-zinc-500 transition-all"
              >
                <input
                  type="text"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  placeholder={`向 @${item.assignee.name} 发送指令 (例如: "重新执行步骤", "分析最近异常原因")...`}
                  disabled={isAgentResponding}
                  className="flex-1 bg-transparent px-2.5 py-1 text-xs text-white placeholder-zinc-500 focus:outline-none disabled:opacity-50"
                />
                <button
                  type="submit"
                  disabled={!chatInput.trim() || isAgentResponding}
                  className="px-3 py-1 bg-white hover:bg-zinc-200 disabled:bg-zinc-700 disabled:text-zinc-400 text-black font-semibold rounded-md text-xs transition-colors flex items-center gap-1 cursor-pointer shrink-0"
                >
                  <span>发送指令</span>
                  <CornerDownLeft className="w-3.5 h-3.5" />
                </button>
              </form>
            </div>
          </div>

          {/* Right Sidebar Meta Area (Right 4 cols) */}
          <div className="lg:col-span-4 space-y-3.5 border-t lg:border-t-0 lg:border-l border-[#333538] pt-4 lg:pt-0 lg:pl-5">
            {/* Status Selector */}
            <div className="bg-[#1e1f20] p-3 rounded-lg border border-[#333538]">
              <label className="block text-[11px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider font-mono">
                当前工单状态
              </label>
              <select
                id="select-item-status"
                value={item.status}
                onChange={(e) =>
                  onStatusChange(item.id, e.target.value as WorkItemStatus)
                }
                className="w-full bg-[#131314] border border-[#333538] text-white rounded-lg p-2 text-xs font-semibold focus:outline-none focus:border-zinc-500"
              >
                <option value="backlog">待办池 (Backlog)</option>
                <option value="todo">待开始 (Todo)</option>
                <option value="in_progress">执行中 (In Progress)</option>
                <option value="in_review">评审中 (In Review)</option>
                <option value="blocked">已阻塞 (Blocked)</option>
                <option value="done">已完成 (Done)</option>
              </select>
              <p className="text-[10px] text-zinc-500 mt-1.5">
                切换状态将即时写入审计流水并更新关联状态机节点。
              </p>
            </div>

            {/* Assignee Card */}
            <div className="bg-[#1e1f20] p-3 rounded-lg border border-[#333538] space-y-2">
              <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider font-mono">
                责任执行 Agent
              </div>
              <div className="flex items-center gap-2.5">
                <div
                  className="w-8 h-8 rounded-full flex items-center justify-center text-black font-bold text-xs bg-white"
                >
                  {item.assignee.name.slice(0, 2)}
                </div>
                <div>
                  <div className="text-xs font-bold text-white">
                    {item.assignee.name}
                  </div>
                  <div className="text-[11px] text-zinc-400">
                    {item.assignee.specialty}
                  </div>
                </div>
              </div>
            </div>

            {/* Reporter Card */}
            <div className="bg-[#1e1f20] p-3 rounded-lg border border-[#333538] text-xs">
              <div className="text-zinc-400 font-semibold mb-1 uppercase tracking-wider text-[11px] font-mono">
                下达创建者
              </div>
              <div className="text-white font-medium">
                {item.reporter.name}
              </div>
              <div className="text-[10px] text-zinc-500 mt-1 font-mono">
                创建时间: {item.createdAt}
              </div>
            </div>

            {/* Progress & Estimation */}
            <div className="bg-[#1e1f20] p-3 rounded-lg border border-[#333538] space-y-2 text-xs">
              <div className="flex justify-between items-center text-zinc-300">
                <span className="font-semibold">交付进度</span>
                <span className="font-bold text-white">{item.progress}%</span>
              </div>
              <div className="w-full h-1.5 bg-[#131314] rounded-full overflow-hidden">
                <div
                  className="h-full bg-white transition-all duration-300"
                  style={{ width: `${item.progress}%` }}
                ></div>
              </div>
              <div className="flex justify-between text-[11px] text-zinc-400 pt-1 font-mono">
                <span>预估: {item.estimatedHours}h</span>
                <span>已用: {item.loggedHours}h</span>
              </div>
            </div>

            {/* Tags */}
            <div className="bg-[#1e1f20] p-3 rounded-lg border border-[#333538]">
              <div className="text-[11px] font-semibold text-zinc-400 mb-2 uppercase tracking-wider flex items-center gap-1 font-mono">
                <Tag className="w-3.5 h-3.5" />
                标签分类
              </div>
              <div className="flex flex-wrap gap-1.5">
                {item.tags.map((t, idx) => (
                  <span
                    key={idx}
                    className="text-[10px] px-2 py-0.5 bg-[#131314] text-zinc-300 rounded border border-[#333538] font-mono"
                  >
                    #{t}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="px-5 py-3 border-t border-[#333538] bg-[#1e1f20] flex items-center justify-between text-xs text-zinc-400">
          <div className="flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-zinc-400" />
            <span>AI Studio 架构引擎 · 全生命周期审计日志 & 实时 Agent 指令交互</span>
          </div>

          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-white hover:bg-zinc-200 text-black font-semibold rounded-md transition-colors cursor-pointer"
          >
            关闭详情
          </button>
        </div>
      </div>

      {/* Render Gitea PRD Document & Annotation Modal */}
      {showPRDModal && isMainWorkItem && (
        <PRDDocumentModal
          item={item}
          onClose={() => setShowPRDModal(false)}
          onUpdatePRDVersions={(updatedVersions, activeVersion, auditLog, chatMsg) => {
            item.prdVersions = updatedVersions;
            item.activePrdVersion = activeVersion;
            if (auditLog) {
              setLocalLogs((prev) => [auditLog, ...prev]);
              setExpandedLogIds((prev) => ({ ...prev, [auditLog.id]: true }));
            }
            if (chatMsg) {
              setLocalChatMessages((prev) => [...prev, chatMsg]);
            }
            if (onUpdatePRDVersions) {
              onUpdatePRDVersions(
                item.id,
                updatedVersions,
                activeVersion,
                auditLog,
                chatMsg
              );
            }
          }}
        />
      )}
    </div>
  );
};
