import React, { useState } from 'react';
import {
  Plus,
  Filter,
  Search,
  Layers,
  GitCommit,
  AlertTriangle,
  Clock,
  ArrowRight,
  Sparkles,
  ChevronRight,
  MoreVertical,
  CheckCircle2,
  TrendingUp,
  Terminal,
  FileText,
} from 'lucide-react';
import { WorkItem, WorkItemStatus, AgentInfo } from '../types';
import { AGENTS } from '../utils/mockData';

interface KanbanBoardProps {
  workItems: WorkItem[];
  onStatusChange: (itemId: string, newStatus: WorkItemStatus) => void;
  onSelectWorkItem: (item: WorkItem) => void;
  onQuickCreateEpic: () => void;
  onAutoDecompose: (item: WorkItem) => void;
}

interface ColumnConfig {
  id: WorkItemStatus;
  title: string;
  badgeBg: string;
  badgeText: string;
  borderColor: string;
}

export const KanbanBoard: React.FC<KanbanBoardProps> = ({
  workItems,
  onStatusChange,
  onSelectWorkItem,
  onQuickCreateEpic,
  onAutoDecompose,
}) => {
  const [filterAgent, setFilterAgent] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [filterType, setFilterType] = useState<string>('all');
  const [draggedItemId, setDraggedItemId] = useState<string | null>(null);

  const columns: ColumnConfig[] = [
    {
      id: 'backlog',
      title: '待办池 (Backlog)',
      badgeBg: 'bg-[#18191b]',
      badgeText: 'text-zinc-400',
      borderColor: 'border-[#333538]',
    },
    {
      id: 'todo',
      title: '待开始 (To Do)',
      badgeBg: 'bg-[#18191b]',
      badgeText: 'text-zinc-300',
      borderColor: 'border-[#333538]',
    },
    {
      id: 'in_progress',
      title: '进行中 (In Progress)',
      badgeBg: 'bg-zinc-800',
      badgeText: 'text-white font-semibold',
      borderColor: 'border-zinc-500',
    },
    {
      id: 'in_review',
      title: '评审中 (In Review)',
      badgeBg: 'bg-zinc-800',
      badgeText: 'text-zinc-200',
      borderColor: 'border-zinc-600',
    },
    {
      id: 'blocked',
      title: '已阻塞 (Blocked)',
      badgeBg: 'bg-red-950/60',
      badgeText: 'text-red-300 font-semibold',
      borderColor: 'border-red-800/80',
    },
    {
      id: 'done',
      title: '已完成 (Done)',
      badgeBg: 'bg-[#18191b]',
      badgeText: 'text-zinc-300 font-semibold',
      borderColor: 'border-zinc-600',
    },
  ];

  // Filtering
  const filteredItems = workItems.filter((item) => {
    if (filterAgent !== 'all' && item.assignee.role !== filterAgent) return false;
    if (filterType === 'epic' && !item.isRootEpic) return false;
    if (filterType === 'subtask' && item.isRootEpic) return false;
    if (
      searchQuery &&
      !item.title.toLowerCase().includes(searchQuery.toLowerCase()) &&
      !item.id.toLowerCase().includes(searchQuery.toLowerCase())
    ) {
      return false;
    }
    return true;
  });

  const handleDragStart = (e: React.DragEvent, id: string) => {
    e.dataTransfer.setData('text/plain', id);
    setDraggedItemId(id);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDrop = (e: React.DragEvent, status: WorkItemStatus) => {
    e.preventDefault();
    const itemId = e.dataTransfer.getData('text/plain') || draggedItemId;
    if (itemId) {
      onStatusChange(itemId, status);
    }
    setDraggedItemId(null);
  };

  return (
    <div
      id="kanban-view-container"
      className="flex-1 flex flex-col h-full bg-[#131314] overflow-hidden text-[#e3e3e3]"
    >
      {/* Top Filter & Action Bar */}
      <div className="p-3 border-b border-[#333538] bg-[#1e1f20] flex flex-wrap items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          {/* Search */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input
              id="input-kanban-search"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索工单 ID 或标题..."
              className="pl-8 pr-3 py-1.5 bg-[#131314] border border-[#333538] text-xs text-white rounded-md placeholder-zinc-500 focus:outline-none focus:border-zinc-500 w-48 sm:w-60"
            />
          </div>

          {/* Filter by Agent */}
          <div className="flex items-center gap-1.5 text-xs text-zinc-400">
            <span className="font-mono">执行者:</span>
            <select
              value={filterAgent}
              onChange={(e) => setFilterAgent(e.target.value)}
              className="bg-[#131314] border border-[#333538] text-zinc-200 text-xs rounded-md px-2 py-1.5 focus:outline-none focus:border-zinc-500"
            >
              <option value="all">所有 Agent (全部)</option>
              <option value="project_agent">Project Orchestrator</option>
              <option value="backend_agent">Backend Core Agent</option>
              <option value="frontend_agent">Frontend UI Agent</option>
              <option value="qa_agent">QA & Security Agent</option>
            </select>
          </div>

          {/* Filter by Type */}
          <div className="flex items-center gap-1.5 text-xs text-zinc-400">
            <span className="font-mono">类型:</span>
            <select
              value={filterType}
              onChange={(e) => setFilterType(e.target.value)}
              className="bg-[#131314] border border-[#333538] text-zinc-200 text-xs rounded-md px-2 py-1.5 focus:outline-none focus:border-zinc-500"
            >
              <option value="all">全部类型 (Epics + Subtasks)</option>
              <option value="epic">仅主任务 (Root Epics)</option>
              <option value="subtask">仅拆解子任务 (Subtasks)</option>
            </select>
          </div>
        </div>

        {/* Right Quick Action: Create New Root Epic */}
        <button
          id="btn-quick-create-epic"
          onClick={onQuickCreateEpic}
          className="px-3 py-1.5 bg-white hover:bg-zinc-200 text-black font-semibold rounded-md text-xs transition-colors flex items-center gap-1.5 cursor-pointer shadow-sm"
        >
          <Plus className="w-3.5 h-3.5 text-black" />
          <span>规划新主工单 (New Epic)</span>
        </button>
      </div>

      {/* Kanban Columns Container */}
      <div className="flex-1 overflow-x-auto p-4 flex gap-4">
        {columns.map((col) => {
          const colItems = filteredItems.filter((i) => i.status === col.id);

          return (
            <div
              key={col.id}
              id={`kanban-col-${col.id}`}
              onDragOver={handleDragOver}
              onDrop={(e) => handleDrop(e, col.id)}
              className="w-72 sm:w-80 flex flex-col bg-[#1e1f20] border border-[#333538] rounded-xl overflow-hidden shrink-0 shadow-sm"
            >
              {/* Column Header */}
              <div className="p-3 border-b border-[#333538] bg-[#18191b] flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-xs text-white">{col.title}</h3>
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded-full font-mono font-bold ${col.badgeBg} ${col.badgeText} border border-[#333538]`}
                  >
                    {colItems.length}
                  </span>
                </div>
              </div>

              {/* Column Cards Drop Area */}
              <div className="flex-1 overflow-y-auto p-2.5 space-y-2.5 min-h-[150px]">
                {colItems.map((item) => {
                  const hasSubtasks = item.subItemIds.length > 0;
                  const isBlocked = item.status === 'blocked';

                  return (
                    <div
                      key={item.id}
                      id={`work-item-card-${item.id}`}
                      draggable
                      onDragStart={(e) => handleDragStart(e, item.id)}
                      onClick={() => onSelectWorkItem(item)}
                      className={`p-3 rounded-lg bg-[#131314] border border-[#333538] hover:border-zinc-400 transition-all cursor-pointer shadow-sm group relative select-none ${
                        isBlocked ? 'border-red-800/80 bg-red-950/20' : ''
                      }`}
                    >
                      {/* Card Header: ID, Priority, Type Tag */}
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-1.5">
                          <span className="font-mono text-xs font-bold text-white px-1.5 py-0.2 bg-[#1e1f20] border border-[#333538] rounded">
                            #{item.id}
                          </span>
                          <span className="text-[10px] font-mono font-semibold px-1.5 py-0.2 rounded bg-[#18191b] text-zinc-300 border border-[#333538]">
                            {item.priority}
                          </span>
                        </div>

                        {item.isRootEpic ? (
                          <div className="flex items-center gap-1">
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-[#1e1f20] text-zinc-200 border border-zinc-600 font-medium flex items-center gap-1">
                              <Layers className="w-2.5 h-2.5 text-zinc-400" />
                              Epic
                            </span>
                            <span className="text-[10px] px-1.5 py-0.2 rounded bg-[#18191b] text-white border border-[#333538] font-mono font-bold flex items-center gap-0.5">
                              <FileText className="w-2.5 h-2.5 text-zinc-400" />
                              {item.activePrdVersion || (item.prdVersions && item.prdVersions[0]?.version) || 'PRD'}
                            </span>
                          </div>
                        ) : (
                          <span className="text-[10px] px-1.5 py-0.2 rounded bg-[#18191b] text-zinc-400 font-mono">
                            Subtask
                          </span>
                        )}
                      </div>

                      {/* Card Title */}
                      <h4 className="text-xs font-semibold text-white leading-snug group-hover:text-zinc-200 transition-colors line-clamp-2">
                        {item.title}
                      </h4>

                      {/* Origin & Parent Work Item Link (产生自哪个 WorkItem) */}
                      {item.parentId ? (
                        <div className="mt-2 text-[11px] text-zinc-400 flex items-center gap-1 bg-[#18191b] px-2 py-1 rounded border border-[#282a2c]">
                          <Layers className="w-3 h-3 text-zinc-500 shrink-0" />
                          <span className="truncate">
                            产生自: <strong className="text-zinc-200 font-mono">#{item.parentId}</strong>
                          </span>
                        </div>
                      ) : null}

                      {/* Assignee (是由谁在执行) & Progress */}
                      <div className="mt-2.5 pt-2 border-t border-[#282a2c] flex items-center justify-between">
                        {/* Assignee Avatar + Name */}
                        <div className="flex items-center gap-1.5">
                          <div
                            className="w-4 h-4 rounded-full flex items-center justify-center text-white text-[8px] font-bold"
                            style={{ backgroundColor: item.assignee.color }}
                          >
                            {item.assignee.name.slice(0, 1)}
                          </div>
                          <span className="text-[11px] text-zinc-300 font-medium truncate max-w-[110px]">
                            {item.assignee.name}
                          </span>
                        </div>

                        {/* Progress or Hours */}
                        <div className="flex items-center gap-2 text-[10px] text-zinc-400 font-mono">
                          {item.auditLogs && item.auditLogs.length > 0 && (
                            <span className="text-zinc-400 flex items-center gap-0.5">
                              <Terminal className="w-2.5 h-2.5 text-zinc-400" />
                              {item.auditLogs.length}
                            </span>
                          )}
                          {item.commits.length > 0 && (
                            <span className="text-zinc-400 flex items-center gap-0.5">
                              <GitCommit className="w-2.5 h-2.5 text-zinc-400" />
                              {item.commits.length}
                            </span>
                          )}
                          <span>{item.progress}%</span>
                        </div>
                      </div>

                      {/* Auto Decompose Trigger Button for Root Epics */}
                      {item.isRootEpic && !hasSubtasks && item.status === 'todo' && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onAutoDecompose(item);
                          }}
                          className="mt-2.5 w-full py-1 bg-white hover:bg-zinc-200 text-black text-[11px] font-semibold rounded flex items-center justify-center gap-1.5 transition-colors cursor-pointer"
                        >
                          <Sparkles className="w-3 h-3 text-black" />
                          <span>启动并自动分解子工单</span>
                        </button>
                      )}

                      {/* Blocked Alert on Card */}
                      {isBlocked && (
                        <div className="mt-2 p-1.5 bg-red-950/40 border border-red-800/60 rounded text-[10px] text-red-300 flex items-center gap-1">
                          <AlertTriangle className="w-3 h-3 text-red-400 shrink-0" />
                          <span className="truncate">{item.bottleneckReason || '执行阻塞'}</span>
                        </div>
                      )}
                    </div>
                  );
                })}

                {colItems.length === 0 && (
                  <div className="h-24 border border-dashed border-[#333538] rounded-lg flex items-center justify-center text-xs text-zinc-500">
                    拖拽工单至此处
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

