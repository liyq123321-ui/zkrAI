import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { LayoutGrid, GitFork, ArrowRight, Sparkles } from 'lucide-react';

interface MiniPreviewWidgetProps {
  currentTab: 'kanban' | 'flow' | 'code' | 'commits';
  onSwitchTab: (tab: 'kanban' | 'flow') => void;
  bottleneckCount: number;
  inProgressCount: number;
}

export const MiniPreviewWidget: React.FC<MiniPreviewWidgetProps> = ({
  currentTab,
  onSwitchTab,
  bottleneckCount,
  inProgressCount,
}) => {
  const [isHovered, setIsHovered] = useState(false);
  const targetTab = currentTab === 'kanban' ? 'flow' : 'kanban';

  return (
    <div
      id="mini-preview-nav-widget"
      className="relative z-30"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <button
        id="btn-switch-preview-view"
        onClick={() => onSwitchTab(targetTab)}
        className="group flex items-center gap-2.5 px-3 py-1.5 rounded-lg border border-[#333538] bg-[#1e1f20] hover:bg-[#282a2c] text-zinc-200 text-xs font-medium transition-all shadow-sm cursor-pointer"
        title={`切换至 ${targetTab === 'flow' ? '任务流转图 (DAG)' : 'Jira 敏捷看板'}`}
      >
        {/* Thumbnail icon representation */}
        <div className="relative w-7 h-5 rounded border border-[#333538] bg-[#131314] overflow-hidden flex items-center justify-center p-0.5">
          {targetTab === 'flow' ? (
            <div className="w-full h-full flex flex-col justify-between p-0.5">
              <div className="flex justify-between items-center">
                <div className="w-1.5 h-1.5 rounded-full bg-white"></div>
                <div className="w-2.5 h-0.5 bg-zinc-500"></div>
                <div className="w-1.5 h-1.5 rounded-full bg-zinc-300"></div>
              </div>
              <div className="flex justify-center">
                <div className="w-1.5 h-1.5 rounded-full bg-zinc-400"></div>
              </div>
            </div>
          ) : (
            <div className="w-full h-full grid grid-cols-3 gap-0.5">
              <div className="bg-[#1e1f20] rounded-xs flex flex-col gap-0.5 p-0.5">
                <div className="w-full h-1 bg-zinc-400 rounded-xxs"></div>
              </div>
              <div className="bg-[#1e1f20] rounded-xs flex flex-col gap-0.5 p-0.5">
                <div className="w-full h-1 bg-zinc-300 rounded-xxs"></div>
                <div className="w-full h-1 bg-zinc-500 rounded-xxs"></div>
              </div>
              <div className="bg-[#1e1f20] rounded-xs flex flex-col gap-0.5 p-0.5">
                <div className="w-full h-1 bg-white rounded-xxs"></div>
              </div>
            </div>
          )}

          {targetTab === 'flow' && bottleneckCount > 0 && (
            <span className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full"></span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          <span className="text-zinc-400">切换视角:</span>
          <span className="font-semibold text-white flex items-center gap-1">
            {targetTab === 'flow' ? (
              <>
                <GitFork className="w-3.5 h-3.5 text-zinc-300" />
                任务流转图 (DAG)
              </>
            ) : (
              <>
                <LayoutGrid className="w-3.5 h-3.5 text-zinc-300" />
                敏捷看板 (Kanban)
              </>
            )}
          </span>
        </div>

        <ArrowRight className="w-3.5 h-3.5 text-zinc-400 group-hover:translate-x-0.5 transition-transform" />
      </button>

      {/* Floating live mini hover card preview */}
      <AnimatePresence>
        {isHovered && (
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.96 }}
            transition={{ duration: 0.15 }}
            className="absolute right-0 top-full mt-2 w-72 p-3 bg-[#1e1f20] rounded-xl border border-[#333538] shadow-2xl z-50 pointer-events-none text-zinc-200"
          >
            <div className="flex items-center justify-between pb-2 mb-2 border-b border-[#333538]">
              <div className="flex items-center gap-2">
                <div className="p-1 rounded-md bg-[#131314] text-white border border-[#333538]">
                  {targetTab === 'flow' ? (
                    <GitFork className="w-3.5 h-3.5" />
                  ) : (
                    <LayoutGrid className="w-3.5 h-3.5" />
                  )}
                </div>
                <span className="text-xs font-semibold text-white">
                  {targetTab === 'flow'
                    ? '状态机流转拓扑预览'
                    : '任务看板实时状态预览'}
                </span>
              </div>
              <span className="text-[10px] px-1.5 py-0.5 bg-[#131314] border border-[#333538] text-zinc-400 rounded font-mono">
                点击切换
              </span>
            </div>

            {/* Visual Mini Graphic */}
            <div className="h-28 w-full bg-[#131314] rounded-lg border border-[#333538] p-2 relative overflow-hidden flex flex-col justify-between">
              {targetTab === 'flow' ? (
                <>
                  <div className="text-[10px] text-zinc-300 font-mono flex items-center justify-between">
                    <span>DAG State Engine</span>
                    {bottleneckCount > 0 ? (
                      <span className="text-red-400 font-semibold flex items-center gap-1">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500"></span>
                        {bottleneckCount} 个瓶颈节点
                      </span>
                    ) : (
                      <span className="text-emerald-400">流转通畅</span>
                    )}
                  </div>
                  <div className="flex items-center justify-between px-2">
                    <div className="w-7 h-7 rounded border border-zinc-600 bg-zinc-800 flex items-center justify-center text-[9px] text-zinc-200 font-mono">
                      Spec
                    </div>
                    <div className="h-0.5 flex-1 bg-zinc-600 mx-1"></div>
                    <div className="w-8 h-8 rounded border border-white bg-zinc-800 flex items-center justify-center text-[9px] text-white font-semibold font-mono shadow-sm">
                      Epic
                    </div>
                    <div className="h-0.5 flex-1 bg-zinc-600 mx-1"></div>
                    <div className="w-7 h-7 rounded border border-zinc-500 bg-zinc-800 flex items-center justify-center text-[9px] text-zinc-300 font-mono">
                      QA
                    </div>
                  </div>
                  <div className="text-[9px] text-zinc-400 flex items-center gap-1 font-mono">
                    <Sparkles className="w-2.5 h-2.5 text-zinc-300" />
                    <span>状态机驱动 · 依赖与时序一览</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="text-[10px] text-zinc-300 font-mono flex items-center justify-between">
                    <span>Jira Agile Kanban</span>
                    <span className="text-zinc-200 font-semibold">
                      {inProgressCount} 个进行中
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-1.5 h-14">
                    <div className="bg-[#1e1f20] border border-[#333538] rounded p-1 flex flex-col gap-1">
                      <span className="text-[8px] text-zinc-400 font-mono">TODO</span>
                      <div className="h-2 bg-[#282a2c] rounded-xs"></div>
                      <div className="h-2 bg-[#282a2c] rounded-xs"></div>
                    </div>
                    <div className="bg-[#1e1f20] border border-zinc-500 rounded p-1 flex flex-col gap-1">
                      <span className="text-[8px] text-white font-mono font-bold">
                        DOING
                      </span>
                      <div className="h-2 bg-zinc-700 rounded-xs"></div>
                      <div className="h-2 bg-zinc-700 rounded-xs"></div>
                    </div>
                    <div className="bg-[#1e1f20] border border-[#333538] rounded p-1 flex flex-col gap-1">
                      <span className="text-[8px] text-zinc-400 font-mono">DONE</span>
                      <div className="h-2 bg-[#282a2c] rounded-xs"></div>
                    </div>
                  </div>
                  <div className="text-[9px] text-zinc-400 flex items-center gap-1 font-mono">
                    <Sparkles className="w-2.5 h-2.5 text-zinc-300" />
                    <span>工单分解与责任 Agent 归属</span>
                  </div>
                </>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

