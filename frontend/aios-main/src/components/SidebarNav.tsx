import React from 'react';
import {
  Bot,
  LayoutGrid,
  GitFork,
  Code2,
  GitCommit,
  Layers,
  Settings,
  Terminal,
  Activity,
  Sparkles,
  Sun,
  Moon,
  HardDrive,
} from 'lucide-react';
import { useTheme } from '../context/ThemeContext';

interface SidebarNavProps {
  activeTab: 'kanban' | 'flow' | 'code' | 'commits';
  onTabChange: (tab: 'kanban' | 'flow' | 'code' | 'commits') => void;
  isChatOpen: boolean;
  onToggleChat: () => void;
  bottleneckCount: number;
  activeTasksCount: number;
  onOpenGoogleDrive?: () => void;
}

export const SidebarNav: React.FC<SidebarNavProps> = ({
  activeTab,
  onTabChange,
  isChatOpen,
  onToggleChat,
  bottleneckCount,
  activeTasksCount,
  onOpenGoogleDrive,
}) => {
  const { theme, toggleTheme } = useTheme();

  const navItems = [
    {
      id: 'kanban' as const,
      name: '敏捷任务看板',
      icon: LayoutGrid,
      badge: activeTasksCount > 0 ? activeTasksCount : undefined,
      badgeColor: 'bg-zinc-700 text-white',
    },
    {
      id: 'flow' as const,
      name: '任务流转拓扑图 (DAG)',
      icon: GitFork,
      badge: bottleneckCount > 0 ? `${bottleneckCount} 阻塞` : undefined,
      badgeColor: 'bg-red-900/80 text-red-200 border border-red-800',
    },
    {
      id: 'code' as const,
      name: '代码工作区与实时差异',
      icon: Code2,
    },
    {
      id: 'commits' as const,
      name: 'Git 提交追溯流',
      icon: GitCommit,
    },
  ];

  return (
    <aside
      id="activity-sidebar-nav"
      className="w-[60px] h-full bg-[#131314] border-r border-[#333538] flex flex-col items-center py-4 space-y-4 select-none z-40 shrink-0"
    >
      {/* Brand / Logo (AI Studio Style) */}
      <div className="mb-1 group relative">
        <button
          id="btn-brand-logo"
          className="w-10 h-10 bg-white text-black rounded-xl flex items-center justify-center font-bold text-xs shadow-md hover:bg-zinc-200 transition-all cursor-pointer"
          title="AI Studio Multi-Agent Orchestrator"
        >
          <Sparkles className="w-5 h-5 text-black" />
        </button>
      </div>

      {/* Primary Agent Chat Trigger */}
      <div className="pb-3 border-b border-[#333538] w-full flex justify-center">
        <button
          id="btn-toggle-agent-chat"
          onClick={onToggleChat}
          className={`relative w-10 h-10 rounded-lg flex items-center justify-center transition-all cursor-pointer ${
            isChatOpen
              ? 'bg-[#282a2c] text-white border border-[#3c4043] shadow-sm'
              : 'text-zinc-400 hover:text-white hover:bg-[#1e1f20]'
          }`}
          title={isChatOpen ? '折叠 Agent 对话面板' : '展开 Agent 对话面板'}
        >
          <Bot className="w-5 h-5" />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-white"></span>
        </button>
      </div>

      {/* Main Workspace Tabs */}
      <nav className="flex-1 flex flex-col space-y-3 w-full px-2 items-center">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeTab === item.id;
          return (
            <button
              key={item.id}
              id={`nav-item-${item.id}`}
              onClick={() => onTabChange(item.id)}
              className={`relative w-10 h-10 rounded-lg flex items-center justify-center transition-all group cursor-pointer ${
                isActive
                  ? 'bg-[#282a2c] text-white border border-[#3c4043] shadow-sm'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-[#1e1f20]'
              }`}
              title={item.name}
            >
              <Icon className="w-5 h-5" />

              {/* Active Indicator Line */}
              {isActive && (
                <span className="absolute -left-2 top-2 bottom-2 w-1 rounded-r-full bg-white"></span>
              )}

              {/* Badge */}
              {item.badge && (
                <span
                  className={`absolute -top-1 -right-1 px-1.5 py-0.2 rounded-full text-[9px] font-mono font-bold leading-tight ${
                    item.badgeColor || 'bg-zinc-700 text-white'
                  }`}
                >
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Bottom Diagnostics / Theme Switcher / Google Drive / Settings */}
      <div className="pt-3 border-t border-[#333538] flex flex-col space-y-2 w-full items-center">
        {/* Google Drive Sync Button */}
        <button
          id="btn-sidebar-google-drive"
          onClick={onOpenGoogleDrive}
          className="relative w-10 h-10 rounded-lg flex items-center justify-center text-zinc-400 hover:text-blue-400 hover:bg-[#1e1f20] transition-colors group cursor-pointer"
          title="Google Drive 同步：将代码保存至 ~/code/frontend"
        >
          <HardDrive className="w-4 h-4 text-zinc-400 group-hover:text-blue-400 transition-colors" />
          <span className="sr-only">Google Drive Sync</span>
        </button>

        {/* GitHub Light/Dark Theme Switcher in Bottom Left */}
        <button
          id="btn-theme-toggle"
          onClick={toggleTheme}
          className={`relative w-10 h-10 rounded-lg flex items-center justify-center transition-all group cursor-pointer ${
            theme === 'light'
              ? 'bg-[#eaecf0] text-amber-600 border border-[#d0d7de] shadow-sm hover:bg-[#d0d7de]'
              : 'text-zinc-400 hover:text-amber-300 hover:bg-[#1e1f20]'
          }`}
          title={theme === 'dark' ? '切换至 GitHub 浅色模式' : '切换至 GitHub 深色模式'}
        >
          {theme === 'dark' ? (
            <Sun className="w-4 h-4 text-zinc-400 group-hover:text-amber-400 transition-colors" />
          ) : (
            <Moon className="w-4 h-4 text-zinc-700 group-hover:text-black transition-colors" />
          )}
          <span className="sr-only">Toggle theme</span>
        </button>

        <button
          className="w-10 h-10 rounded-lg flex items-center justify-center text-zinc-400 hover:text-white hover:bg-[#1e1f20] transition-colors cursor-pointer"
          title="系统健康度与状态监控"
        >
          <Activity className="w-4 h-4" />
        </button>

        <button
          className="w-10 h-10 rounded-lg flex items-center justify-center text-zinc-400 hover:text-white hover:bg-[#1e1f20] transition-colors cursor-pointer"
          title="环境偏好与设置"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>
    </aside>
  );
};

