import React, { useState, useRef } from 'react';
import {
  GitFork,
  Activity,
  AlertTriangle,
  CheckCircle2,
  PlayCircle,
  Clock,
  Zap,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Sparkles,
  ArrowRight,
  Layers,
  ShieldAlert,
  Wrench,
  Bot,
  ExternalLink,
  Info,
} from 'lucide-react';
import { FlowNode, FlowEdge, WorkItem, AgentInfo } from '../types';
import { AGENTS } from '../utils/mockData';

interface TaskFlowDiagramProps {
  nodes: FlowNode[];
  edges: FlowEdge[];
  workItems: WorkItem[];
  onSelectWorkItem: (item: WorkItem) => void;
  onUnblockBottleneck: (nodeId: string) => void;
  onAddNextStageNode: () => void;
}

export const TaskFlowDiagram: React.FC<TaskFlowDiagramProps> = ({
  nodes,
  edges,
  workItems,
  onSelectWorkItem,
  onUnblockBottleneck,
  onAddNextStageNode,
}) => {
  const [zoomLevel, setZoomLevel] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [selectedNode, setSelectedNode] = useState<FlowNode | null>(null);
  const [filterBottlenecksOnly, setFilterBottlenecksOnly] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);

  const bottleneckNodes = nodes.filter((n) => n.bottleneck || n.status === 'blocked');

  const handleMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.flow-node-card')) return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    setPanOffset({
      x: e.clientX - dragStart.x,
      y: e.clientY - dragStart.y,
    });
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const resetView = () => {
    setZoomLevel(1);
    setPanOffset({ x: 0, y: 0 });
  };

  // Node Type config in AI Studio neutral tones
  const typeConfig: Record<
    string,
    { label: string; color: string; border: string; bg: string }
  > = {
    start: {
      label: '起点 / 意图',
      color: 'text-zinc-200',
      border: 'border-[#3c4043]',
      bg: 'bg-[#1e1f20]',
    },
    process: {
      label: '架构分解',
      color: 'text-zinc-200',
      border: 'border-[#3c4043]',
      bg: 'bg-[#1e1f20]',
    },
    agent_action: {
      label: 'Agent 研发',
      color: 'text-white font-semibold',
      border: 'border-zinc-500',
      bg: 'bg-[#282a2c]',
    },
    condition: {
      label: '质量门禁 / 审计',
      color: 'text-zinc-200',
      border: 'border-[#3c4043]',
      bg: 'bg-[#1e1f20]',
    },
    review: {
      label: '全栈联调',
      color: 'text-zinc-200',
      border: 'border-[#3c4043]',
      bg: 'bg-[#1e1f20]',
    },
    deploy: {
      label: '发布部署',
      color: 'text-zinc-200',
      border: 'border-[#3c4043]',
      bg: 'bg-[#1e1f20]',
    },
    end: {
      label: '里程碑归档',
      color: 'text-zinc-400',
      border: 'border-[#333538]',
      bg: 'bg-[#18191b]',
    },
  };

  return (
    <div
      id="task-flow-diagram-container"
      className="flex-1 flex flex-col h-full bg-[#131314] relative overflow-hidden select-none text-[#e3e3e3]"
    >
      {/* Top Diagram Toolbar & Bottleneck Monitor Banner */}
      <div className="p-3 border-b border-[#333538] bg-[#1e1f20] z-20 flex flex-wrap items-center justify-between gap-3 shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-white text-black font-bold">
              <GitFork className="w-4 h-4" />
            </div>
            <div>
              <h2 className="text-xs font-bold text-white flex items-center gap-1.5">
                DAG Flow Map (状态机驱动任务流转)
                <span className="text-[10px] px-2 py-0.2 bg-[#131314] text-zinc-300 border border-[#333538] rounded font-mono">
                  {nodes.length} 节点 / {edges.length} 依赖流向
                </span>
              </h2>
              <p className="text-[10px] text-zinc-400 font-mono">
                实时计算上/下游依赖链路，高亮执行阻塞瓶颈，动态扩展后续任务
              </p>
            </div>
          </div>
        </div>

        {/* View Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setFilterBottlenecksOnly(!filterBottlenecksOnly)}
            className={`px-2.5 py-1 text-xs rounded-md border font-medium transition-all flex items-center gap-1.5 cursor-pointer ${
              filterBottlenecksOnly
                ? 'bg-red-600 text-white border-red-500'
                : 'bg-[#131314] text-zinc-300 border-[#333538] hover:bg-[#282a2c]'
            }`}
          >
            <AlertTriangle className="w-3.5 h-3.5" />
            仅看瓶颈 ({bottleneckNodes.length})
          </button>

          <div className="flex items-center bg-[#131314] border border-[#333538] rounded-md p-0.5">
            <button
              onClick={() => setZoomLevel((z) => Math.max(0.6, z - 0.1))}
              className="p-1.5 text-zinc-400 hover:text-white rounded cursor-pointer"
              title="缩小"
            >
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
            <span className="text-[11px] font-mono px-2 text-zinc-300">
              {Math.round(zoomLevel * 100)}%
            </span>
            <button
              onClick={() => setZoomLevel((z) => Math.min(1.6, z + 0.1))}
              className="p-1.5 text-zinc-400 hover:text-white rounded cursor-pointer"
              title="放大"
            >
              <ZoomIn className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={resetView}
              className="p-1.5 text-zinc-400 hover:text-white rounded border-l border-[#333538] cursor-pointer"
              title="重置视图"
            >
              <Maximize2 className="w-3.5 h-3.5" />
            </button>
          </div>

          <button
            id="btn-add-flow-node"
            onClick={onAddNextStageNode}
            className="flex items-center gap-1.5 px-3 py-1 bg-white hover:bg-zinc-200 text-black text-xs font-semibold rounded-md shadow-sm transition-all cursor-pointer"
            title="模拟状态机动态生成下一阶段衍生节点"
          >
            <Sparkles className="w-3.5 h-3.5 text-black" />
            <span>动态生成后续任务</span>
          </button>
        </div>
      </div>

      {/* Critical Bottleneck Alert Banner */}
      {bottleneckNodes.length > 0 && (
        <div className="px-4 py-2 bg-red-950/80 border-b border-red-800/80 flex items-center justify-between text-xs z-10">
          <div className="flex items-center gap-2 text-red-200">
            <ShieldAlert className="w-4 h-4 text-red-400 shrink-0" />
            <span>
              <strong>当前检测到关键路径瓶颈</strong>: 节点{' '}
              <span className="font-mono font-bold text-white bg-red-900/60 px-1.5 py-0.5 rounded border border-red-500/40">
                {bottleneckNodes[0].title}
              </span>{' '}
              处于阻塞状态，下游全栈联调将受阻。
            </span>
          </div>

          <button
            id="btn-resolve-flow-bottleneck"
            onClick={() => onUnblockBottleneck(bottleneckNodes[0].id)}
            className="px-3 py-1 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-md shadow-sm flex items-center gap-1 transition-all cursor-pointer"
          >
            <Wrench className="w-3.5 h-3.5 text-black" />
            一键解除沙箱阻塞并恢复流转
          </button>
        </div>
      )}

      {/* Main Canvas Area */}
      <div
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        className="flex-1 relative cursor-grab active:cursor-grabbing overflow-hidden bg-[#131314]"
      >
        {/* Canvas World Transform Container */}
        <div
          style={{
            transform: `translate(${panOffset.x}px, ${panOffset.y}px) scale(${zoomLevel})`,
            transformOrigin: '0 0',
            transition: isDragging ? 'none' : 'transform 0.1s ease-out',
          }}
          className="absolute inset-0 w-[2400px] h-[1400px] pointer-events-auto"
        >
          {/* SVG Dependency Lines */}
          <svg className="absolute inset-0 w-full h-full pointer-events-none z-0">
            <defs>
              <marker
                id="arrow-normal"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 10 5 L 0 9 z" fill="#80868B" />
              </marker>

              <marker
                id="arrow-active"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 10 5 L 0 9 z" fill="#FFFFFF" />
              </marker>

              <marker
                id="arrow-bottleneck"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto-start-reverse"
              >
                <path d="M 0 1 L 10 5 L 0 9 z" fill="#F87171" />
              </marker>
            </defs>

            {edges.map((edge) => {
              const sourceNode = nodes.find((n) => n.id === edge.source);
              const targetNode = nodes.find((n) => n.id === edge.target);
              if (!sourceNode || !targetNode) return null;

              const sx = sourceNode.x + 230;
              const sy = sourceNode.y + 48;
              const tx = targetNode.x;
              const ty = targetNode.y + 48;

              const dx = Math.max(40, (tx - sx) / 2);
              const pathD = `M ${sx} ${sy} C ${sx + dx} ${sy}, ${tx - dx} ${ty}, ${tx} ${ty}`;

              return (
                <g key={edge.id}>
                  {edge.isBottleneck ? (
                    <path
                      d={pathD}
                      fill="none"
                      stroke="#F87171"
                      strokeWidth="3"
                      strokeDasharray="6 4"
                      className="animate-pulse opacity-80"
                    />
                  ) : edge.active ? (
                    <path
                      d={pathD}
                      fill="none"
                      stroke="#FFFFFF"
                      strokeWidth="2"
                      opacity="0.8"
                    />
                  ) : (
                    <path
                      d={pathD}
                      fill="none"
                      stroke="#333538"
                      strokeWidth="1.5"
                      strokeDasharray="4 4"
                    />
                  )}

                  <path
                    d={pathD}
                    fill="none"
                    stroke={
                      edge.isBottleneck
                        ? '#F87171'
                        : edge.active
                        ? '#FFFFFF'
                        : '#333538'
                    }
                    strokeWidth={edge.active ? 2 : 1.5}
                    markerEnd={`url(#${
                      edge.isBottleneck
                        ? 'arrow-bottleneck'
                        : edge.active
                        ? 'arrow-active'
                        : 'arrow-normal'
                    })`}
                  />
                </g>
              );
            })}
          </svg>

          {/* Flow Nodes Elements */}
          {nodes
            .filter((node) => (!filterBottlenecksOnly ? true : node.bottleneck || node.status === 'blocked'))
            .map((node) => {
              const isBottleneck = node.bottleneck || node.status === 'blocked';
              const isRunning = node.status === 'running';
              const isCompleted = node.status === 'completed';
              const config = typeConfig[node.type] || typeConfig.process;
              const relatedItem = node.relatedWorkItemId
                ? workItems.find((w) => w.id === node.relatedWorkItemId)
                : null;

              return (
                <div
                  key={node.id}
                  id={`flow-node-${node.id}`}
                  onClick={() => {
                    setSelectedNode(node);
                    if (relatedItem) onSelectWorkItem(relatedItem);
                  }}
                  style={{ left: `${node.x}px`, top: `${node.y}px` }}
                  className={`flow-node-card absolute w-60 bg-[#1e1f20] border rounded-xl p-3 shadow-lg transition-all cursor-pointer z-10 select-none ${
                    isBottleneck
                      ? 'border-red-600 ring-2 ring-red-900/50 bg-red-950/20'
                      : isRunning
                      ? 'border-white ring-1 ring-zinc-500'
                      : 'border-[#333538] hover:border-zinc-500'
                  } ${node.dynamicSpawned ? 'border-dashed' : ''}`}
                >
                  {/* Node Header: Type & Status */}
                  <div className="flex items-center justify-between mb-1.5">
                    <span
                      className={`text-[10px] px-1.5 py-0.2 rounded font-mono font-semibold ${config.bg} ${config.color} border ${config.border}`}
                    >
                      {config.label}
                    </span>

                    <div className="flex items-center gap-1.5">
                      {isBottleneck ? (
                        <span className="flex items-center gap-1 text-[10px] text-red-400 font-bold font-mono">
                          <AlertTriangle className="w-3 h-3" />
                          瓶颈阻塞
                        </span>
                      ) : isRunning ? (
                        <span className="flex items-center gap-1 text-[10px] text-white font-mono font-bold">
                          <span className="w-2 h-2 rounded-full bg-white animate-pulse"></span>
                          执行中
                        </span>
                      ) : isCompleted ? (
                        <span className="flex items-center gap-1 text-[10px] text-zinc-300 font-mono font-bold">
                          <CheckCircle2 className="w-3 h-3 text-zinc-300" />
                          已完成
                        </span>
                      ) : (
                        <span className="text-[10px] text-zinc-500 font-mono">
                          排队待命
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Title */}
                  <h4 className="text-xs font-bold text-white leading-snug line-clamp-2">
                    {node.title}
                  </h4>

                  {/* Agent & WorkItem association */}
                  <div className="mt-2 pt-2 border-t border-[#282a2c] flex items-center justify-between text-[10px] text-zinc-400 font-mono">
                    <div className="flex items-center gap-1.5">
                      <div
                        className="w-3.5 h-3.5 rounded-full flex items-center justify-center text-white text-[7px] font-bold"
                        style={{ backgroundColor: node.assignedAgent.color }}
                      >
                        {node.assignedAgent.name.slice(0, 1)}
                      </div>
                      <span className="truncate max-w-[90px]">
                        {node.assignedAgent.name.split(' ')[0]}
                      </span>
                    </div>

                    {node.relatedWorkItemId && (
                      <span className="text-white px-1.5 py-0.2 bg-[#131314] rounded border border-[#333538]">
                        #{node.relatedWorkItemId}
                      </span>
                    )}
                  </div>

                  {/* Bottleneck message */}
                  {isBottleneck && node.bottleneckMsg && (
                    <div className="mt-2 p-1.5 bg-red-950/60 rounded border border-red-800 text-[10px] text-red-200">
                      {node.bottleneckMsg}
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
};

