import React from 'react';
import {
  GitCommit,
  GitPullRequest,
  FileCode,
  CheckCircle2,
  User,
  Clock,
  ArrowRight,
} from 'lucide-react';
import { WorkItem } from '../types';

interface CommitHistoryTabProps {
  workItems: WorkItem[];
  onSelectWorkItem: (item: WorkItem) => void;
}

export const CommitHistoryTab: React.FC<CommitHistoryTabProps> = ({
  workItems,
  onSelectWorkItem,
}) => {
  const allCommits = workItems
    .flatMap((item) =>
      item.commits.map((c) => ({
        ...c,
        parentWorkItem: item,
      }))
    )
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

  return (
    <div
      id="commit-history-container"
      className="flex-1 flex flex-col h-full bg-[#131314] p-6 overflow-y-auto text-[#e3e3e3]"
    >
      <div className="max-w-4xl mx-auto w-full space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-[#333538]">
          <div>
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <GitCommit className="w-5 h-5 text-white" />
              Git 代码提交追踪流水线 (Agent Commit Stream)
            </h2>
            <p className="text-xs text-zinc-400 mt-1">
              全量沉淀各专职 Agent 自动生成并关联工单的代码变更与 Diff 历史
            </p>
          </div>
          <span className="text-xs font-mono px-3 py-1 bg-[#1e1f20] text-zinc-200 border border-[#333538] rounded-full font-bold">
            共 {allCommits.length} 条已追踪提交
          </span>
        </div>

        {/* Commit List Timeline */}
        <div className="space-y-4 relative before:absolute before:left-4 before:top-2 before:bottom-2 before:w-0.5 before:bg-[#333538]">
          {allCommits.map((commit) => (
            <div key={commit.id} className="relative pl-10 group">
              {/* Timeline Dot */}
              <div className="absolute left-2.5 top-3 -translate-x-1/2 w-3.5 h-3.5 rounded-full bg-white border-4 border-[#131314] group-hover:scale-125 transition-transform"></div>

              <div className="p-4 rounded-xl bg-[#1e1f20] border border-[#333538] hover:border-zinc-500 transition-all shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-white px-2 py-0.5 bg-[#131314] border border-[#333538] rounded">
                      {commit.shortHash}
                    </span>
                    <span className="text-xs font-bold text-white">
                      {commit.message}
                    </span>
                  </div>

                  <div className="flex items-center gap-2 text-xs">
                    <button
                      onClick={() => onSelectWorkItem(commit.parentWorkItem)}
                      className="px-2 py-0.5 bg-[#131314] text-zinc-300 hover:text-white border border-[#333538] hover:border-zinc-500 rounded font-mono font-medium flex items-center gap-1 transition-colors cursor-pointer"
                    >
                      关联 #{commit.parentWorkItem.id}
                      <ArrowRight className="w-3 h-3" />
                    </button>
                    <span className="text-zinc-500 font-mono text-[11px]">
                      {commit.timestamp}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-3 text-xs text-zinc-400 mb-3 font-mono">
                  <div className="flex items-center gap-1.5">
                    <span
                      className="w-2 h-2 rounded-full"
                      style={{ backgroundColor: commit.author.color }}
                    ></span>
                    <span className="text-zinc-300 font-medium">
                      {commit.author.name}
                    </span>
                  </div>
                  <span>•</span>
                  <div>
                    <span className="text-emerald-400 font-mono font-bold">
                      +{commit.insertions}
                    </span>
                    <span className="text-red-400 font-mono font-bold ml-1.5">
                      -{commit.deletions}
                    </span>
                  </div>
                  <span>•</span>
                  <span>{commit.filesChanged} 个变更文件</span>
                </div>

                {/* Diff snippets */}
                <div className="space-y-2">
                  {commit.files.map((file, fIdx) => (
                    <div
                      key={fIdx}
                      className="rounded-lg bg-[#131314] border border-[#333538] overflow-hidden text-[11px]"
                    >
                      <div className="px-3 py-1.5 bg-[#18191b] border-b border-[#333538] flex items-center justify-between text-zinc-300 font-mono">
                        <div className="flex items-center gap-2">
                          <FileCode className="w-3.5 h-3.5 text-zinc-400" />
                          <span>{file.name}</span>
                        </div>
                        <span className="text-[10px] text-zinc-200 font-bold uppercase px-1.5 py-0.2 bg-zinc-800 border border-zinc-700 rounded">
                          {file.status}
                        </span>
                      </div>
                      <pre className="p-3 font-mono text-zinc-200 bg-[#0e0e10] overflow-x-auto whitespace-pre leading-relaxed">
                        {file.diffSnippet}
                      </pre>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
