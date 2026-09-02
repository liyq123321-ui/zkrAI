/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from 'react';
import { SidebarNav } from './components/SidebarNav';
import { AgentChat } from './components/AgentChat';
import { KanbanBoard } from './components/KanbanBoard';
import { TaskFlowDiagram } from './components/TaskFlowDiagram';
import { MiniPreviewWidget } from './components/MiniPreviewWidget';
import { WorkItemDetailModal } from './components/WorkItemDetailModal';
import { CodeWorkspaceTab } from './components/CodeWorkspaceTab';
import { CommitHistoryTab } from './components/CommitHistoryTab';
import { GoogleDriveModal } from './components/GoogleDriveModal';
import {
  INITIAL_WORK_ITEMS,
  INITIAL_FLOW_NODES,
  INITIAL_FLOW_EDGES,
  INITIAL_CHAT_MESSAGES,
  AGENTS,
} from './utils/mockData';
import {
  WorkItem,
  WorkItemStatus,
  FlowNode,
  FlowEdge,
  AgentChatMessage,
  AgentInfo,
  ExecutionAuditLog,
  PRDVersion,
} from './types';
import {
  createTransitionRecord,
  generateSubItemsForEpic,
  createNewEpicFromPrompt,
} from './utils/workflowEngine';
import {
  LayoutGrid,
  GitFork,
  Code2,
  GitCommit,
  Bot,
  Layers,
  Sparkles,
  Zap,
  Sun,
  Moon,
  HardDrive,
} from 'lucide-react';
import { useTheme } from './context/ThemeContext';

export default function MockApp() {
  const { theme, toggleTheme } = useTheme();
  const [activeTab, setActiveTab] = useState<'kanban' | 'flow' | 'code' | 'commits'>('kanban');
  const [isChatOpen, setIsChatOpen] = useState(true);
  const [isGoogleDriveOpen, setIsGoogleDriveOpen] = useState(false);
  const [workItems, setWorkItems] = useState<WorkItem[]>(INITIAL_WORK_ITEMS);
  const [flowNodes, setFlowNodes] = useState<FlowNode[]>(INITIAL_FLOW_NODES);
  const [flowEdges, setFlowEdges] = useState<FlowEdge[]>(INITIAL_FLOW_EDGES);
  const [messages, setMessages] = useState<AgentChatMessage[]>(INITIAL_CHAT_MESSAGES);
  const [selectedWorkItem, setSelectedWorkItem] = useState<WorkItem | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);

  const bottleneckCount = flowNodes.filter((n) => n.bottleneck || n.status === 'blocked').length;
  const inProgressCount = workItems.filter((w) => w.status === 'in_progress').length;

  // Handle Work Item Status Change
  const handleStatusChange = (itemId: string, newStatus: WorkItemStatus) => {
    let newlySpawnedItems: WorkItem[] = [];
    let updatedParent: WorkItem | null = null;

    setWorkItems((prev) =>
      prev.map((item) => {
        if (item.id !== itemId) return item;
        if (item.status === newStatus) return item;

        const transition = createTransitionRecord(
          item.status,
          newStatus,
          AGENTS.project_agent,
          `工单状态从 [${item.status}] 变更为 [${newStatus}]`
        );

        let subIds = [...item.subItemIds];

        // If moving a Root Epic to In Progress and it hasn't spawned child items yet
        if (item.isRootEpic && newStatus === 'in_progress' && item.subItemIds.length === 0) {
          const { subItems, newNodes, newEdges } = generateSubItemsForEpic(item);
          newlySpawnedItems = subItems;
          subIds = subItems.map((s) => s.id);
          transition.reason = `主任务移入 In Progress，自动触发工单分解引擎并下发 ${subItems.length} 个专职子任务。`;

          // Synchronize State Machine Graph
          setFlowNodes((prevNodes) => [...prevNodes, ...newNodes]);
          setFlowEdges((prevEdges) => [...prevEdges, ...newEdges]);

          // Send notification in Agent chat
          const nowStr = new Date().toTimeString().slice(0, 5);
          const notificationMsg: AgentChatMessage = {
            id: `msg-${Date.now()}`,
            sender: AGENTS.project_agent,
            content: `🚀 **主工单 [${item.id}] 已启动**！工单分解引擎已下发以下专职子任务：\n${subItems
              .map((s) => `- **${s.id}** [${s.assignee.name}]: ${s.title}`)
              .join('\n')}\n\n已自动更新状态机拓扑图节点与流转链路。`,
            timestamp: nowStr,
            thoughtChain: [
              `主任务 ${item.id} 状态跃迁至 in_progress`,
              '启动拓扑分解引擎 (Task Decomposition Subsystem)',
              `生成 ${subItems.length} 个下级执行节点并挂载至主干链路`,
            ],
            actionType: 'spawn_subtasks',
            actionPayload: {
              createdWorkItemId: item.id,
              spawnedSubItemIds: subIds,
            },
          };
          setMessages((prevMsg) => [...prevMsg, notificationMsg]);
        }

        const updated: WorkItem = {
          ...item,
          status: newStatus,
          subItemIds: subIds,
          progress: newStatus === 'done' ? 100 : newStatus === 'in_progress' ? 50 : item.progress,
          transitionHistory: [transition, ...item.transitionHistory],
          updatedAt: new Date().toISOString().replace('T', ' ').slice(0, 16),
          bottleneckReason: newStatus === 'blocked' ? item.bottleneckReason || '人工或环境依赖标记阻塞' : undefined,
        };

        updatedParent = updated;
        return updated;
      })
    );

    if (newlySpawnedItems.length > 0) {
      setWorkItems((prev) => [...prev, ...newlySpawnedItems]);
    }

    // Sync corresponding State Machine node
    setFlowNodes((prevNodes) =>
      prevNodes.map((node) => {
        if (node.relatedWorkItemId === itemId) {
          const flowStatus =
            newStatus === 'done'
              ? 'completed'
              : newStatus === 'in_progress'
              ? 'running'
              : newStatus === 'blocked'
              ? 'blocked'
              : 'idle';
          return {
            ...node,
            status: flowStatus,
            bottleneck: newStatus === 'blocked',
          };
        }
        return node;
      })
    );

    // Keep selected item inspector in sync
    if (selectedWorkItem && selectedWorkItem.id === itemId && updatedParent) {
      setSelectedWorkItem(updatedParent);
    }
  };

  // Auto Decompose handler from Kanban button
  const handleAutoDecompose = (item: WorkItem) => {
    handleStatusChange(item.id, 'in_progress');
  };

  // Unblock Bottleneck
  const handleUnblockBottleneck = (nodeId: string) => {
    setFlowNodes((prev) =>
      prev.map((node) => {
        if (node.id === nodeId || node.bottleneck) {
          return {
            ...node,
            status: 'running',
            bottleneck: false,
            bottleneckMsg: undefined,
            duration: 'Active (Unblocked)',
          };
        }
        return node;
      })
    );

    // Update corresponding work item if any
    setWorkItems((prev) =>
      prev.map((w) => {
        if (w.status === 'blocked') {
          const tr = createTransitionRecord(
            'blocked',
            'in_progress',
            AGENTS.qa_agent,
            '沙箱依赖 Redis 集群已就绪，解除阻塞标记并恢复安全审计。'
          );
          return {
            ...w,
            status: 'in_progress',
            bottleneckReason: undefined,
            transitionHistory: [tr, ...w.transitionHistory],
          };
        }
        return w;
      })
    );

    // Add resolution message in chat
    const nowStr = new Date().toTimeString().slice(0, 5);
    setMessages((prev) => [
      ...prev,
      {
        id: `msg-unblock-${Date.now()}`,
        sender: AGENTS.qa_agent,
        content: `✅ **瓶颈已消除**：沙箱依赖已完成注入，WI-104 安全审计任务已恢复运行，状态机流向恢复正常。`,
        timestamp: nowStr,
        thoughtChain: [
          '检测到沙箱 Redis 依赖就绪信号',
          '重置 WorkItem 状态从 blocked 跃迁至 in_progress',
          '广播状态机总线清除 isBottleneck 标志',
        ],
      },
    ]);
  };

  // Add Dynamic Next Stage Node to State Machine
  const handleAddNextStageNode = () => {
    const timestamp = Date.now();
    const newNodeId = `node_dyn_${timestamp.toString().slice(-4)}`;
    const randomY = 120 + Math.floor(Math.random() * 240);

    const newNode: FlowNode = {
      id: newNodeId,
      title: `自动化负载压测与 SLA 门禁 (${newNodeId})`,
      type: 'condition',
      status: 'running',
      assignedAgent: AGENTS.qa_agent,
      inputs: ['node_integration'],
      outputs: ['node_deploy_staging'],
      bottleneck: false,
      duration: 'In Progress (3m)',
      latencyMs: 190,
      x: 1380,
      y: randomY,
      description: '动态衍生状态节点：针对高并发 API 执行 5,000 QPS 链路压测与熔断校验。',
      dynamicSpawned: true,
    };

    const newEdge: FlowEdge = {
      id: `edge-dyn-${timestamp}`,
      source: 'node_integration',
      target: newNodeId,
      active: true,
    };

    setFlowNodes((prev) => [...prev, newNode]);
    setFlowEdges((prev) => [...prev, newEdge]);

    const nowStr = new Date().toTimeString().slice(0, 5);
    setMessages((prev) => [
      ...prev,
      {
        id: `msg-dyn-${timestamp}`,
        sender: AGENTS.architect_agent,
        content: `⚡ **状态机动态扩展**：基于流转现状，已生成衍生节点 **[${newNode.title}]** 并插入至 DAG 拓扑中。`,
        timestamp: nowStr,
        thoughtChain: [
          '分析下游部署风险 -> 判定需要高并发 SLA 压力测试节点',
          `注入动态节点 ${newNodeId} 至 node_integration 与 node_deploy_staging 之间`,
          '重绘 DAG 拓扑结构',
        ],
      },
    ]);
  };

  // Send Message & Handle Agent Planning
  const handleSendMessage = (text: string, preferredAgent: AgentInfo = AGENTS.project_agent) => {
    const nowStr = new Date().toTimeString().slice(0, 5);

    const userMsg: AgentChatMessage = {
      id: `msg-${Date.now()}-user`,
      sender: AGENTS.user,
      content: text,
      timestamp: nowStr,
    };

    setMessages((prev) => [...prev, userMsg]);
    setIsProcessing(true);

    setTimeout(() => {
      // Create new Epic and plan
      const { newEpic, newNode, newEdge } = createNewEpicFromPrompt(text);

      setWorkItems((prev) => [newEpic, ...prev]);
      setFlowNodes((prev) => [...prev, newNode]);
      setFlowEdges((prev) => [...prev, newEdge]);

      const agentReply: AgentChatMessage = {
        id: `msg-${Date.now()}-reply`,
        sender: preferredAgent,
        content: `🎯 **已完成需求澄清与架构规划**：\n已在看板生成主工单 **[${newEpic.id}] ${newEpic.title}**。\n\n- **初始状态**：\`To Do\`\n- **预估工时**：${newEpic.estimatedHours}h\n- **执行策略**：您可以点击看板卡片或将其拖入 \`In Progress\` 列，触发自动任务拆解。已同步在状态机流转图中接入主干节点。`,
        timestamp: new Date().toTimeString().slice(0, 5),
        thoughtChain: [
          `解析需求语义: "${text.slice(0, 30)}..."`,
          `生成主工单工件 ${newEpic.id} 并指派至敏捷看板待办池`,
          `在状态机 DAG 拓扑中挂载新主干节点 ${newNode.id}`,
        ],
        actionType: 'propose_task',
        actionPayload: {
          createdWorkItemId: newEpic.id,
        },
      };

      setMessages((prev) => [...prev, agentReply]);
      setIsProcessing(false);
    }, 900);
  };

  // Update WorkItem Logs & Chat from within Modal
  const handleUpdateWorkItemLogsAndChat = (
    itemId: string,
    newLog?: ExecutionAuditLog,
    newChat?: AgentChatMessage
  ) => {
    setWorkItems((prev) =>
      prev.map((w) => {
        if (w.id !== itemId) return w;
        return {
          ...w,
          auditLogs: newLog ? [newLog, ...w.auditLogs] : w.auditLogs,
          itemChatMessages: newChat
            ? [...(w.itemChatMessages || []), newChat]
            : w.itemChatMessages,
        };
      })
    );

    if (selectedWorkItem && selectedWorkItem.id === itemId) {
      setSelectedWorkItem((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          auditLogs: newLog ? [newLog, ...prev.auditLogs] : prev.auditLogs,
          itemChatMessages: newChat
            ? [...(prev.itemChatMessages || []), newChat]
            : prev.itemChatMessages,
        };
      });
    }
  };

  // Update WorkItem PRD Versions & notify
  const handleUpdateWorkItemPRD = (
    itemId: string,
    updatedVersions: PRDVersion[],
    activeVersion: string,
    auditLog?: ExecutionAuditLog,
    chatMessage?: AgentChatMessage
  ) => {
    setWorkItems((prev) =>
      prev.map((w) => {
        if (w.id !== itemId) return w;
        return {
          ...w,
          prdVersions: updatedVersions,
          activePrdVersion: activeVersion,
          auditLogs: auditLog ? [auditLog, ...w.auditLogs] : w.auditLogs,
          itemChatMessages: chatMessage
            ? [...(w.itemChatMessages || []), chatMessage]
            : w.itemChatMessages,
        };
      })
    );

    if (selectedWorkItem && selectedWorkItem.id === itemId) {
      setSelectedWorkItem((prev) => {
        if (!prev) return null;
        return {
          ...prev,
          prdVersions: updatedVersions,
          activePrdVersion: activeVersion,
          auditLogs: auditLog ? [auditLog, ...prev.auditLogs] : prev.auditLogs,
          itemChatMessages: chatMessage
            ? [...(prev.itemChatMessages || []), chatMessage]
            : prev.itemChatMessages,
        };
      });
    }

    if (chatMessage) {
      setMessages((prev) => [...prev, chatMessage]);
    }
  };

  // Quick Action Handler
  const handleTriggerQuickAction = (actionKey: string) => {
    if (actionKey === 'create_gateway_epic') {
      handleSendMessage('构建高并发分布式 API 网关与速率限制器 (Rate Limiter)');
    } else if (actionKey === 'create_analytics_epic') {
      handleSendMessage('构建实时指标分析与数据监控看板');
    } else if (actionKey === 'start_wi_101') {
      handleStatusChange('WI-101', 'in_progress');
    } else if (actionKey === 'unblock_wi_104') {
      handleUnblockBottleneck('node_security_audit');
    }
  };

  const handleQuickCreateEpic = () => {
    handleSendMessage('构建企业级微服务通信网格与服务注册发现中心');
  };

  return (
    <div
      id="codex-app-root"
      className="flex h-screen w-screen bg-[#131314] text-[#e3e3e3] font-sans overflow-hidden select-none"
    >
      {/* 1. Far Left Activity Bar */}
      <SidebarNav
        activeTab={activeTab}
        onTabChange={(t) => setActiveTab(t)}
        isChatOpen={isChatOpen}
        onToggleChat={() => setIsChatOpen(!isChatOpen)}
        bottleneckCount={bottleneckCount}
        activeTasksCount={inProgressCount}
        onOpenGoogleDrive={() => setIsGoogleDriveOpen(true)}
      />

      {/* 2. Left Agent Conversation Dialog */}
      {isChatOpen && (
        <AgentChat
          messages={messages}
          onSendMessage={handleSendMessage}
          onTriggerQuickAction={handleTriggerQuickAction}
          workItems={workItems}
          onSelectWorkItem={(item) => setSelectedWorkItem(item)}
          isProcessing={isProcessing}
        />
      )}

      {/* 3. Right Workspace Main Pane */}
      <main className="flex-1 flex flex-col h-full overflow-hidden bg-[#131314] relative">
        {/* Workspace Top Tab Header & Mini Preview Quick Switcher */}
        <header className="h-10 bg-[#1e1f20] border-b border-[#333538] px-4 flex items-center justify-between shrink-0 z-30">
          {/* Main Tabs Navigation */}
          <div className="flex items-center space-x-1 h-full">
            <button
              id="tab-btn-kanban"
              onClick={() => setActiveTab('kanban')}
              className={`flex items-center gap-2 px-3.5 h-full text-xs font-medium transition-all cursor-pointer ${
                activeTab === 'kanban'
                  ? 'border-b-2 border-white text-white font-semibold'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <LayoutGrid className="w-3.5 h-3.5 text-zinc-300" />
              <span>Kanban Board (敏捷看板)</span>
              <span className="px-1.5 py-0.2 rounded text-[10px] bg-[#131314] text-zinc-300 border border-[#333538] font-mono">
                {workItems.length}
              </span>
            </button>

            <button
              id="tab-btn-flow"
              onClick={() => setActiveTab('flow')}
              className={`flex items-center gap-2 px-3.5 h-full text-xs font-medium transition-all cursor-pointer ${
                activeTab === 'flow'
                  ? 'border-b-2 border-white text-white font-semibold'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <GitFork className="w-3.5 h-3.5 text-zinc-300" />
              <span>DAG Flow Map (任务流转图)</span>
              {bottleneckCount > 0 && (
                <span className="px-1.5 py-0.2 rounded text-[10px] bg-red-950/60 text-red-400 border border-red-800/80 font-mono font-bold">
                  {bottleneckCount} 阻塞
                </span>
              )}
            </button>

            <button
              id="tab-btn-code"
              onClick={() => setActiveTab('code')}
              className={`flex items-center gap-2 px-3.5 h-full text-xs font-medium transition-all cursor-pointer ${
                activeTab === 'code'
                  ? 'border-b-2 border-white text-white font-semibold'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <Code2 className="w-3.5 h-3.5 text-zinc-400" />
              <span>Code Editor</span>
            </button>

            <button
              id="tab-btn-commits"
              onClick={() => setActiveTab('commits')}
              className={`flex items-center gap-2 px-3.5 h-full text-xs font-medium transition-all cursor-pointer ${
                activeTab === 'commits'
                  ? 'border-b-2 border-white text-white font-semibold'
                  : 'text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <GitCommit className="w-3.5 h-3.5 text-zinc-400" />
              <span>Git Commits</span>
            </button>
          </div>

          {/* Top Right Actions: Google Drive Sync & Mini Preview Widget */}
          <div className="flex items-center space-x-2">
            <button
              id="btn-header-google-drive"
              onClick={() => setIsGoogleDriveOpen(true)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded bg-blue-600/10 hover:bg-blue-600/20 text-blue-400 hover:text-blue-300 border border-blue-500/30 text-xs font-medium transition-colors cursor-pointer"
              title="将代码同步保存至 Google Drive (~/code/frontend)"
            >
              <HardDrive className="w-3.5 h-3.5 text-blue-400" />
              <span className="hidden sm:inline font-mono">Google Drive (~/code/frontend)</span>
            </button>

            <MiniPreviewWidget
              currentTab={activeTab}
              onSwitchTab={(target) => setActiveTab(target)}
              bottleneckCount={bottleneckCount}
              inProgressCount={inProgressCount}
            />
          </div>
        </header>

        {/* Tab Content Display Area */}
        <div className="flex-1 overflow-hidden relative pb-8">
          {activeTab === 'kanban' && (
            <KanbanBoard
              workItems={workItems}
              onStatusChange={handleStatusChange}
              onSelectWorkItem={(item) => setSelectedWorkItem(item)}
              onQuickCreateEpic={handleQuickCreateEpic}
              onAutoDecompose={handleAutoDecompose}
            />
          )}

          {activeTab === 'flow' && (
            <TaskFlowDiagram
              nodes={flowNodes}
              edges={flowEdges}
              workItems={workItems}
              onSelectWorkItem={(item) => setSelectedWorkItem(item)}
              onUnblockBottleneck={handleUnblockBottleneck}
              onAddNextStageNode={handleAddNextStageNode}
            />
          )}

          {activeTab === 'code' && (
            <CodeWorkspaceTab onOpenGoogleDrive={() => setIsGoogleDriveOpen(true)} />
          )}

          {activeTab === 'commits' && (
            <CommitHistoryTab
              workItems={workItems}
              onSelectWorkItem={(item) => setSelectedWorkItem(item)}
            />
          )}
        </div>

        {/* Bottom AI Studio Status Bar */}
        <div className="absolute bottom-0 right-0 left-0 h-8 px-4 bg-[#1e1f20] border-t border-[#333538] flex items-center justify-between z-20 text-[10px] text-zinc-400 font-mono">
          <div className="flex items-center space-x-3">
            <span className="text-zinc-200 font-semibold flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
              AI STUDIO ENGINE
            </span>
            <span className="hidden sm:inline text-zinc-500">|</span>
            <span className="hidden sm:inline">UTF-8</span>
            <span className="hidden sm:inline text-zinc-500">|</span>
            <button
              id="btn-status-theme-toggle"
              onClick={toggleTheme}
              className="flex items-center gap-1.5 px-2 py-0.5 rounded bg-[#131314] hover:bg-[#282a2c] text-zinc-300 hover:text-white border border-[#333538] transition-colors cursor-pointer"
              title={theme === 'dark' ? '切换为 GitHub 浅色模式' : '切换为 GitHub 深色模式'}
            >
              {theme === 'dark' ? (
                <>
                  <Sun className="w-3 h-3 text-amber-400" />
                  <span>GitHub Dark</span>
                </>
              ) : (
                <>
                  <Moon className="w-3 h-3 text-blue-600" />
                  <span>GitHub Light</span>
                </>
              )}
            </button>
          </div>

          <div className="flex items-center space-x-3">
            <div className="flex items-center space-x-1.5">
              <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></div>
              <span className="text-zinc-200 font-bold tracking-tight">AGENT NETWORK ONLINE</span>
            </div>
            <span className="text-[9px] px-1.5 py-0.5 bg-[#131314] border border-[#333538] rounded text-zinc-300">
              {inProgressCount} ACTIVE
            </span>
          </div>
        </div>
      </main>

      {/* 4. Deep Work Item Detail & Audit Modal */}
      {selectedWorkItem && (
        <WorkItemDetailModal
          item={selectedWorkItem}
          allWorkItems={workItems}
          onClose={() => setSelectedWorkItem(null)}
          onStatusChange={handleStatusChange}
          onSelectWorkItem={(item) => setSelectedWorkItem(item)}
          onUpdateWorkItemLogsAndChat={handleUpdateWorkItemLogsAndChat}
          onUpdatePRDVersions={handleUpdateWorkItemPRD}
        />
      )}

      {/* 5. Google Drive Export & Sync Modal */}
      <GoogleDriveModal
        isOpen={isGoogleDriveOpen}
        onClose={() => setIsGoogleDriveOpen(false)}
      />
    </div>
  );
}
