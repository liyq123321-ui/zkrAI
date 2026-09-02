import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  FileText,
  GitBranch,
  GitCommit,
  History,
  MessageSquarePlus,
  Send,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
  Copy,
  Check,
  X,
  ExternalLink,
  ChevronRight,
  ChevronDown,
  Sparkles,
  Layers,
  Bot,
  User,
  Plus,
  Trash2,
  FileCode,
  Download,
  Eye,
  Edit3,
  GitPullRequest,
  CheckCheck,
  ArrowRight,
  CornerDownLeft,
} from 'lucide-react';
import { WorkItem, PRDVersion, PRDComment, AgentInfo, ExecutionAuditLog, AgentChatMessage } from '../types';
import { AGENTS } from '../utils/mockData';

interface PRDDocumentModalProps {
  item: WorkItem;
  onClose: () => void;
  onUpdatePRDVersions: (
    updatedVersions: PRDVersion[],
    activeVersion: string,
    auditLog?: ExecutionAuditLog,
    chatMessage?: AgentChatMessage
  ) => void;
}

interface StagedCommentDraft {
  id: string;
  sectionId: string;
  sectionTitle: string;
  targetQuote: string;
  content: string;
}

export const PRDDocumentModal: React.FC<PRDDocumentModalProps> = ({
  item,
  onClose,
  onUpdatePRDVersions,
}) => {
  const [activeTab, setActiveTab] = useState<'document' | 'history' | 'diff'>('document');
  const [selectedVersionTag, setSelectedVersionTag] = useState<string>(
    item.activePrdVersion || (item.prdVersions && item.prdVersions[0]?.version) || 'v1.1'
  );

  // Staged comments for batch submission
  const [stagedComments, setStagedComments] = useState<StagedCommentDraft[]>([]);
  const [activeCommentingSection, setActiveCommentingSection] = useState<{
    id: string;
    title: string;
    quote: string;
  } | null>(null);
  const [commentInputText, setCommentInputText] = useState('');
  const [copiedNotification, setCopiedNotification] = useState<string | null>(null);

  // Agent generation state
  const [isSynthesizing, setIsSynthesizing] = useState(false);
  const [synthesisStep, setSynthesisStep] = useState<string>('');

  const prdVersions: PRDVersion[] = item.prdVersions || [];
  const currentVersionData =
    prdVersions.find((v) => v.version === selectedVersionTag) ||
    prdVersions[0] || {
      version: 'v1.0',
      title: `${item.title} - PRD 需求规格说明书`,
      createdAt: '2026-08-25 09:30',
      author: AGENTS.project_agent,
      giteaCommit: {
        repo: 'gitea.corp.ai/enterprise/core-auth',
        branch: 'main',
        commitHash: '8f9a12c',
        filePath: 'docs/PRD_CORE_AUTH.md',
      },
      summary: '初始版本：基于用户意图与架构规划生成核心需求规格。',
      content: `# ${item.title}\n\n## 1.0 项目背景与业务目标\n构建高可用企业级认证鉴权系统...\n\n## 2.0 用户角色与核心场景\n支持 Admin、Developer、Viewer 三级角色...\n\n## 3.0 核心 API 契约与认证协议\n采用 JWT RS256 签名体系与 Redis 黑名单机制...\n\n## 4.0 2FA 双因子安全与 TOTP 流程\n支持 TOTP 算法及动态验证码...\n\n## 5.0 RBAC 权限矩阵与策略\n定义资源粒度的权限矩阵...\n\n## 6.0 质量门禁与验收标准\n测试覆盖率需达 90% 以上，支持 5,000 QPS 稳定运行。`,
      comments: [],
      isActive: true,
    };

  // Section list parsed for inline annotations
  const docSections = [
    {
      id: 'sec-1',
      title: '1.0 项目背景与业务目标 (Background & Goals)',
      content: `### 1.1 业务诉求
当前微服务生态急需一套统一的中心化身份验证与授权系统，解决各业务线自建 Auth 导致的凭证格式不一致、权限越权漏洞及审计盲区。

### 1.2 核心目标 (KPIs)
- **多租户安全隔离**：全平台单点登录 (SSO) 与会话隔离，支持跨组织无缝鉴权。
- **高并发低延迟**：认证中间件验证开销控制在 < 5ms，支持 5,000 QPS 峰值。
- **合规审计**：全量操作行为记录至不可篡改的日志流水中，满足金融级安全合规。`,
    },
    {
      id: 'sec-2',
      title: '2.0 用户角色与核心用例 (User Roles & Stories)',
      content: `### 2.1 角色定义矩阵
- **SuperAdmin (超级管理员)**：全局租户配置、密钥轮换、解封账号、安全策略下发。
- **Engineer (工程师 / 开发者)**：应用 API Key 申请、OAuth 客户端注册、沙箱联调。
- **Viewer (观察者)**：只读查看应用列表与状态监控，禁止敏感凭证导出。

### 2.2 核心用户故事 (User Stories)
1. **US-01 账号密码 + 2FA 登录**：用户输入企业邮箱与密码后，若开启 2FA 则进入 TOTP 六位动态口令验证界面。
2. **US-02 会话主动注销与黑名单吊销**：用户点击注销或密码重置时，即刻将当前 JWT 的 \`jti\` 写入 Redis 黑名单，剩余 TTL 期间全网拒绝。
3. **US-03 权限越权拦截**：未携带特定 Scope 令牌的请求在 API 网关层直接返回 403 Forbidden 并记录告警。`,
    },
    {
      id: 'sec-3',
      title: '3.0 核心 API 契约与鉴权协议 (API Contracts & JWT Protocol)',
      content: `### 3.1 令牌签发规范
- **算法标准**：RS256 非对称加密 (私钥由 Auth Server 保管，公钥暴露至 \`/.well-known/jwks.json\`)。
- **Token 结构**：
  - \`iss\`: \`https://auth.corp.ai\`
  - \`sub\`: \`user_id_uuid\`
  - \`role\`: \`admin | engineer | viewer\`
  - \`jti\`: \`jwt_unique_id\` (防重放与黑名单依据)
  - \`exp\`: \`1h (默认) / 7d (Refresh Token)\`

### 3.2 核心端点规范
\`\`\`http
POST /api/v1/auth/login
Content-Type: application/json
{
  "email": "lead@company.ai",
  "password": "SecurePassword123!"
}

Response (200 OK):
{
  "status": "REQUIRE_2FA",
  "tempToken": "eyJh...temp_session_token",
  "expiresIn": 300
}
\`\`\`

\`\`\`http
POST /api/v1/auth/verify-2fa
Content-Type: application/json
{
  "tempToken": "eyJh...temp_session_token",
  "totpCode": "849201"
}

Response (200 OK):
{
  "accessToken": "eyJh...jwt_token",
  "refreshToken": "rt_89f02c...",
  "user": { "id": "u-101", "email": "lead@company.ai", "role": "admin" }
}
\`\`\``,
    },
    {
      id: 'sec-4',
      title: '4.0 2FA 双因子安全与 TOTP 流程 (2FA Security Architecture)',
      content: `### 4.1 算法实现与防碰撞
- 基于 RFC 6238 TOTP (Time-based One-Time Password) 标准，时间步长 30 秒，容忍前后各 1 个步长窗口以应对客户端时钟微小漂移。
- 密钥生成采用 Base32 编码 160-bit 随机种子，生成兼容 Google Authenticator / 1Password 的 \`otpauth://\` URI 与二维码。

### 4.2 暴力破解防护 (Brute-Force Guard)
- 单 IP / 单账号在 10 分钟内连续输错 5 次口令，自动冻结 15 分钟并触发邮件安全预警。
- 针对黑产字典攻击，集成 Redis 滑动窗口限流器 (Rate Limiter)。`,
    },
    {
      id: 'sec-5',
      title: '5.0 RBAC 权限矩阵与策略 (RBAC Policy Matrix)',
      content: `### 5.1 权限点与资源映射表
| 资源模块 | 权限码 | SuperAdmin | Engineer | Viewer |
| :--- | :--- | :---: | :---: | :---: |
| 用户管理 | \`user:read\` / \`user:write\` | ✅ / ✅ | ✅ / ❌ | ✅ / ❌ |
| 密钥管控 | \`secret:rotate\` / \`secret:read\` | ✅ / ✅ | ❌ / ❌ | ❌ / ❌ |
| 服务部署 | \`deploy:staging\` / \`deploy:prod\` | ✅ / ✅ | ✅ / ❌ | ❌ / ❌ |
| 审计流水 | \`audit:export\` | ✅ | ✅ | ❌ |

### 5.2 动态拦截器工作流
中间件读取请求路径与 HTTP Method，匹配路由注解上的 \`@RequirePermission("user:write")\`，通过 bitmask 高速校验用户 Token 中的权限集。`,
    },
    {
      id: 'sec-6',
      title: '6.0 质量门禁、安全合规与 SLA 验收标准 (Acceptance Criteria & SLA)',
      content: `### 6.1 单元测试与端到端覆盖
- 核心鉴权中间件单元测试覆盖率需达到 **≥ 95%** (包含 JWT 篡改、过期、黑名单等 20+ 异常用例)。
- 前端登录组件需通过 Vitest + Testing Library 交互模拟测试。

### 6.2 性能与 SLA 验收门禁
- **压测指标**：在 5,000 QPS 持续压力下，Token 验证中间件 P99 延迟 ≤ 4.5ms。
- **高可用指标**：Redis 黑名单集群故障时自动降级至本地 LRU 缓存并告警，服务可用性保持 99.99%。`,
    },
  ];

  // Copy Markdown Content
  const handleCopyMarkdown = () => {
    navigator.clipboard.writeText(currentVersionData.content);
    setCopiedNotification('已成功复制完整 PRD Markdown 文档');
    setTimeout(() => setCopiedNotification(null), 2500);
  };

  // Add Comment to Staging
  const handleStageComment = () => {
    if (!activeCommentingSection || !commentInputText.trim()) return;

    const newDraft: StagedCommentDraft = {
      id: `draft-${Date.now()}-${Math.random().toString(36).slice(2, 5)}`,
      sectionId: activeCommentingSection.id,
      sectionTitle: activeCommentingSection.title,
      targetQuote: activeCommentingSection.quote,
      content: commentInputText.trim(),
    };

    setStagedComments((prev) => [...prev, newDraft]);
    setCommentInputText('');
    setActiveCommentingSection(null);
  };

  // Delete staged comment
  const handleRemoveStagedComment = (id: string) => {
    setStagedComments((prev) => prev.filter((c) => c.id !== id));
  };

  // Submit All Staged Comments & Trigger PM Agent Iteration
  const handleSubmitAllComments = () => {
    if (stagedComments.length === 0) return;

    setIsSynthesizing(true);
    setSynthesisStep('PM Agent 正在读取并语义解析所选批注...');

    setTimeout(() => {
      setSynthesisStep('分析架构变更范围，调整 PRD 规格章节与 API 契约...');

      setTimeout(() => {
        setSynthesisStep('生成新版 Markdown 文档，创建 Gitea Commit 提交...');

        setTimeout(() => {
          // Calculate new version tag e.g. v1.1 -> v1.2
          const currentVerNumber = parseFloat(currentVersionData.version.replace('v', '')) || 1.0;
          const nextVerTag = `v${(currentVerNumber + 0.1).toFixed(1)}`;
          const nowStr = new Date().toISOString().replace('T', ' ').slice(0, 16);
          const shortHash = Math.random().toString(16).slice(2, 9);

          // Convert staged comments to persistent comments
          const resolvedComments: PRDComment[] = stagedComments.map((draft, idx) => ({
            id: `comm-${Date.now()}-${idx}`,
            sectionId: draft.sectionId,
            sectionTitle: draft.sectionTitle,
            targetQuote: draft.targetQuote,
            content: draft.content,
            author: AGENTS.user,
            createdAt: nowStr,
            resolved: true,
            resolutionNote: `PM Agent 已理解并在 ${nextVerTag} 中采纳：针对 "${draft.sectionTitle}" 补充了相应规格说明。`,
          }));

          // Updated version content reflecting feedback
          const updatedMarkdown = `# ${item.title} (${nextVerTag} 迭代版)

> **版本说明**：本版本由 PM Agent 响应 Tech Lead 批注 (${stagedComments.length} 条需求变更) 重新综合生成，并同步至 Gitea 仓库。
> **Gitea Commit**: \`${shortHash}\` (main: docs/PRD_CORE_AUTH.md)

${docSections
  .map((sec) => {
    const matchedComments = stagedComments.filter((c) => c.sectionId === sec.id);
    let extraNotes = '';
    if (matchedComments.length > 0) {
      extraNotes = `\n\n> 💡 **${nextVerTag} 变更修正**：\n${matchedComments
        .map((c) => `> - 已响应批注需求："${c.content}"，完成技术规格修订与边界补充。`)
        .join('\n')}`;
    }
    return `## ${sec.title}\n${sec.content}${extraNotes}`;
  })
  .join('\n\n')}`;

          const newVersion: PRDVersion = {
            version: nextVerTag,
            title: `${item.title} - PRD ${nextVerTag}`,
            createdAt: nowStr,
            author: AGENTS.project_agent,
            giteaCommit: {
              repo: 'gitea.corp.ai/enterprise/core-auth',
              branch: 'main',
              commitHash: shortHash,
              commitUrl: `https://gitea.corp.ai/enterprise/core-auth/commit/${shortHash}`,
              filePath: 'docs/PRD_CORE_AUTH.md',
            },
            summary: `响应 Tech Lead 的 ${stagedComments.length} 条评审批注：${stagedComments
              .map((c) => c.content.slice(0, 18))
              .join('；')}...`,
            content: updatedMarkdown,
            comments: resolvedComments,
            isActive: true,
          };

          // Mark previous versions as inactive
          const updatedList: PRDVersion[] = [
            newVersion,
            ...prdVersions.map((v) => ({ ...v, isActive: false })),
          ];

          // Create audit log
          const newAuditLog: ExecutionAuditLog = {
            id: `log-prd-${Date.now()}`,
            timestamp: nowStr,
            level: 'SUCCESS',
            stepName: `PRD 规格迭代更新 (${nextVerTag}) & Gitea Commit 推送`,
            phase: 'planning',
            executor: AGENTS.project_agent,
            summary: `PM Agent 综合用户 ${stagedComments.length} 条批注，生成新版 PRD ${nextVerTag} 并推送至 Gitea 仓库。`,
            durationMs: 480,
            details: {
              inputParams: {
                previousVersion: currentVersionData.version,
                targetVersion: nextVerTag,
                commentsProcessed: stagedComments.length,
              },
              stdout: `[GITEA] Pushing commit ${shortHash} to branch main...\n[SUCCESS] docs/PRD_CORE_AUTH.md updated to ${nextVerTag}.\n[INFO] Auto-notified subsystem agents (Backend, QA, Frontend).`,
              outputResult: {
                version: nextVerTag,
                commitHash: shortHash,
                resolvedCount: stagedComments.length,
              },
              exitCode: 0,
            },
          };

          // Create Agent chat notification
          const newChatMessage: AgentChatMessage = {
            id: `msg-prd-${Date.now()}`,
            sender: AGENTS.project_agent,
            content: `📑 **PRD 已迭代升级至 [${nextVerTag}]**！\n我已理解您提交的 **${stagedComments.length} 条批注**并完成技术规格重构：\n${stagedComments
              .map((c) => `- **[${c.sectionTitle.split(' ')[0]}]**: ${c.content}`)
              .join('\n')}\n\n已生成新版本并同步提交至 Gitea 仓库 (\`${shortHash}\`)。`,
            timestamp: new Date().toTimeString().slice(0, 5),
            thoughtChain: [
              `接收用户批注集 (${stagedComments.length} 条)`,
              `推导架构与 API 契约变更点`,
              `编译生成 PRD ${nextVerTag} Markdown 文档`,
              `触发 Gitea Webhook 并记录版本审计快照`,
            ],
            actionType: 'log_update',
          };

          onUpdatePRDVersions(updatedList, nextVerTag, newAuditLog, newChatMessage);
          setSelectedVersionTag(nextVerTag);
          setStagedComments([]);
          setIsSynthesizing(false);
        }, 600);
      }, 600);
    }, 700);
  };

  // Rollback to specific PRD Version
  const handleRollbackToVersion = (targetVersion: PRDVersion) => {
    const nowStr = new Date().toISOString().replace('T', ' ').slice(0, 16);
    const updatedList = prdVersions.map((v) => ({
      ...v,
      isActive: v.version === targetVersion.version,
    }));

    const rollbackLog: ExecutionAuditLog = {
      id: `log-rb-${Date.now()}`,
      timestamp: nowStr,
      level: 'WARN',
      stepName: `PRD 规格版本回滚至 [${targetVersion.version}]`,
      phase: 'planning',
      executor: AGENTS.project_agent,
      summary: `Tech Lead 手动回滚 PRD 活跃版本至 ${targetVersion.version} (${targetVersion.giteaCommit.commitHash})。`,
      durationMs: 120,
      details: {
        inputParams: { targetVersion: targetVersion.version, reason: 'Manual Tech Lead Rollback' },
        stdout: `[GITEA] HEAD reset to ${targetVersion.giteaCommit.commitHash}.\n[SUCCESS] Active PRD switched to ${targetVersion.version}.`,
        exitCode: 0,
      },
    };

    const rollbackChat: AgentChatMessage = {
      id: `msg-rb-${Date.now()}`,
      sender: AGENTS.project_agent,
      content: `⏪ **PRD 活跃版本已回滚至 [${targetVersion.version}]**。\n当前技术实现与任务流转将以该版本的规格定义为准。`,
      timestamp: new Date().toTimeString().slice(0, 5),
      thoughtChain: [
        `执行版本回滚指令 -> 目标版本: ${targetVersion.version}`,
        `更新 Gitea 指针至 commit: ${targetVersion.giteaCommit.commitHash}`,
        `通知下游 Agent 同步更新理解上下文`,
      ],
      actionType: 'state_change',
    };

    onUpdatePRDVersions(updatedList, targetVersion.version, rollbackLog, rollbackChat);
    setSelectedVersionTag(targetVersion.version);
    setActiveTab('document');
  };

  return (
    <div
      id="modal-prd-document"
      className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-5 bg-black/85 backdrop-blur-md select-none text-[#e3e3e3]"
    >
      <div className="w-full max-w-6xl bg-[#131314] border border-[#333538] rounded-xl shadow-2xl overflow-hidden flex flex-col h-[94vh]">
        {/* 1. Gitea Header Bar */}
        <div className="px-5 py-3 border-b border-[#333538] bg-[#1e1f20] flex items-center justify-between gap-4 shrink-0">
          <div className="flex items-center gap-3 flex-wrap">
            {/* Gitea Brand Badge */}
            <div className="flex items-center gap-2 px-2.5 py-1 bg-[#131314] border border-[#333538] rounded-lg">
              <div className="w-4 h-4 rounded bg-white text-black flex items-center justify-center font-bold text-[10px]">
                G
              </div>
              <span className="text-xs font-bold text-white tracking-wide">Gitea Docs</span>
              <span className="text-zinc-500 text-xs">/</span>
              <span className="text-xs font-mono text-zinc-300">
                {currentVersionData.giteaCommit.repo}
              </span>
            </div>

            {/* Branch & Path */}
            <div className="flex items-center gap-1.5 text-xs text-zinc-400 font-mono">
              <GitBranch className="w-3.5 h-3.5 text-zinc-400" />
              <span className="text-zinc-300">{currentVersionData.giteaCommit.branch}</span>
              <span>:</span>
              <span className="text-white font-semibold">
                {currentVersionData.giteaCommit.filePath}
              </span>
            </div>

            {/* Commit Hash Pill */}
            <div className="flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 bg-[#18191b] border border-[#333538] rounded text-zinc-300">
              <GitCommit className="w-3 h-3 text-zinc-400" />
              <span>{currentVersionData.giteaCommit.commitHash}</span>
            </div>

            {/* Active Version Indicator */}
            <div className="flex items-center gap-1.5 text-xs">
              <span className="px-2 py-0.5 rounded-full bg-white text-black font-mono font-bold text-[11px]">
                {currentVersionData.version}
              </span>
              {currentVersionData.isActive && (
                <span className="text-[10px] text-zinc-400 font-mono flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-white animate-pulse"></span>
                  当前生效规格
                </span>
              )}
            </div>
          </div>

          {/* Right Action Controls */}
          <div className="flex items-center gap-2">
            {/* Version Switcher Dropdown */}
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-zinc-400 font-mono hidden sm:inline">版本:</span>
              <select
                value={selectedVersionTag}
                onChange={(e) => setSelectedVersionTag(e.target.value)}
                className="bg-[#131314] text-xs text-white border border-[#333538] rounded-md px-2.5 py-1 focus:outline-none focus:border-zinc-500 font-mono font-semibold cursor-pointer"
              >
                {prdVersions.map((v) => (
                  <option key={v.version} value={v.version}>
                    {v.version} {v.isActive ? '(生效中)' : '(历史)'} - {v.createdAt}
                  </option>
                ))}
              </select>
            </div>

            <button
              onClick={handleCopyMarkdown}
              className="p-1.5 text-zinc-400 hover:text-white bg-[#131314] hover:bg-[#282a2c] border border-[#333538] rounded-md transition-colors cursor-pointer"
              title="复制 Markdown 原文"
            >
              <Copy className="w-4 h-4" />
            </button>

            <button
              id="btn-close-prd-modal"
              onClick={onClose}
              className="p-1.5 text-zinc-400 hover:text-white hover:bg-[#282a2c] rounded-md transition-colors cursor-pointer"
              title="关闭 PRD 窗口"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 2. Top Nav Tabs */}
        <div className="px-5 border-b border-[#333538] bg-[#18191b] flex items-center justify-between shrink-0">
          <div className="flex items-center gap-6 text-xs font-semibold">
            <button
              onClick={() => setActiveTab('document')}
              className={`py-2.5 border-b-2 flex items-center gap-1.5 transition-colors cursor-pointer ${
                activeTab === 'document'
                  ? 'border-white text-white font-bold'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <FileText className="w-3.5 h-3.5" />
              <span>PRD 需求规格文档 (Markdown 渲染)</span>
            </button>

            <button
              id="tab-prd-history"
              onClick={() => setActiveTab('history')}
              className={`py-2.5 border-b-2 flex items-center gap-1.5 transition-colors cursor-pointer ${
                activeTab === 'history'
                  ? 'border-white text-white font-bold'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200'
              }`}
            >
              <History className="w-3.5 h-3.5" />
              <span>版本演进历史与全量批注审计 ({prdVersions.length} 个版本)</span>
            </button>
          </div>

          {/* Staged Comments Badge on Tab Bar */}
          {stagedComments.length > 0 && activeTab === 'document' && (
            <div className="flex items-center gap-2 py-1">
              <span className="text-[11px] text-zinc-300 font-mono">
                已暂存 <strong>{stagedComments.length}</strong> 条批注待提交
              </span>
              <button
                onClick={handleSubmitAllComments}
                disabled={isSynthesizing}
                className="px-3 py-1 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-md transition-all flex items-center gap-1 cursor-pointer shadow-sm"
              >
                <Sparkles className="w-3.5 h-3.5 text-black" />
                <span>一键提交并让 PM Agent 生成新版 PRD</span>
              </button>
            </div>
          )}
        </div>

        {/* Copy Notification Toast */}
        {copiedNotification && (
          <div className="bg-emerald-950 border-b border-emerald-800 text-emerald-200 px-4 py-1.5 text-xs text-center font-mono">
            {copiedNotification}
          </div>
        )}

        {/* 3. Main Modal Body */}
        <div className="flex-1 overflow-hidden flex bg-[#131314]">
          {/* TAB 1: DOCUMENT & INTERACTIVE INLINE ANNOTATIONS */}
          {activeTab === 'document' && (
            <div className="flex-1 flex overflow-hidden">
              {/* Center PRD Markdown Document Viewer */}
              <div className="flex-1 overflow-y-auto p-6 lg:p-8 space-y-6">
                {/* PRD Title Banner */}
                <div className="pb-4 border-b border-[#333538]">
                  <div className="flex items-center gap-2 text-xs text-zinc-400 font-mono mb-1">
                    <FileCode className="w-4 h-4 text-zinc-400" />
                    <span>PRODUCT REQUIREMENTS DOCUMENT</span>
                    <span>•</span>
                    <span>AUTHOR: {currentVersionData.author.name}</span>
                    <span>•</span>
                    <span>CREATED: {currentVersionData.createdAt}</span>
                  </div>
                  <h1 className="text-xl font-bold text-white tracking-tight">
                    {item.title}
                  </h1>
                  <p className="text-xs text-zinc-300 mt-1.5 leading-relaxed">
                    {currentVersionData.summary}
                  </p>
                </div>

                {/* Section Cards with Inline Comment Triggers */}
                <div className="space-y-5">
                  {docSections.map((section, idx) => {
                    const sectionComments = (currentVersionData.comments || []).filter(
                      (c) => c.sectionId === section.id
                    );
                    const isDraftingThis = activeCommentingSection?.id === section.id;
                    const stagedCount = stagedComments.filter((c) => c.sectionId === section.id).length;

                    return (
                      <div
                        key={section.id}
                        id={`prd-section-${section.id}`}
                        className={`group relative p-5 rounded-xl border transition-all ${
                          isDraftingThis
                            ? 'bg-[#1e1f20] border-white ring-1 ring-zinc-500'
                            : 'bg-[#18191b] border-[#333538] hover:border-zinc-500'
                        }`}
                      >
                        {/* Section Header */}
                        <div className="flex items-center justify-between mb-3">
                          <div className="flex items-center gap-2">
                            <span className="text-[11px] font-mono font-bold text-zinc-300 px-2 py-0.5 bg-[#131314] border border-[#333538] rounded">
                              § {idx + 1}
                            </span>
                            <h2 className="text-sm font-bold text-white">
                              {section.title}
                            </h2>
                          </div>

                          {/* Action to Add Comment to this section */}
                          <div className="flex items-center gap-2">
                            {stagedCount > 0 && (
                              <span className="text-[10px] px-2 py-0.5 rounded-full bg-white text-black font-mono font-bold">
                                {stagedCount} 条批注待提交
                              </span>
                            )}

                            <button
                              onClick={() => {
                                setActiveCommentingSection({
                                  id: section.id,
                                  title: section.title,
                                  quote: section.content.slice(0, 80) + '...',
                                });
                                setCommentInputText('');
                              }}
                              className="px-2.5 py-1 rounded bg-[#1e1f20] hover:bg-[#282a2c] text-zinc-300 hover:text-white border border-[#333538] hover:border-zinc-500 text-xs font-medium flex items-center gap-1.5 transition-colors cursor-pointer"
                            >
                              <MessageSquarePlus className="w-3.5 h-3.5 text-zinc-400" />
                              <span>在此章节写批注</span>
                            </button>
                          </div>
                        </div>

                        {/* Markdown Content Section Body */}
                        <div className="text-xs text-zinc-300 space-y-3 leading-relaxed whitespace-pre-wrap font-sans">
                          {section.content}
                        </div>

                        {/* Inline Historical Comments for this Section */}
                        {sectionComments.length > 0 && (
                          <div className="mt-4 pt-3 border-t border-[#282a2c] space-y-2">
                            <div className="text-[10px] font-mono text-zinc-400 uppercase tracking-wider flex items-center gap-1">
                              <CheckCheck className="w-3 h-3 text-emerald-400" />
                              <span>本章节历史已解决批注 ({sectionComments.length})</span>
                            </div>
                            {sectionComments.map((comm) => (
                              <div
                                key={comm.id}
                                className="p-2.5 rounded-lg bg-[#131314] border border-[#333538] text-xs space-y-1.5"
                              >
                                <div className="flex items-center justify-between text-[11px] text-zinc-400">
                                  <span className="font-semibold text-zinc-200">
                                    {comm.author.name}
                                  </span>
                                  <span className="font-mono text-[10px]">{comm.createdAt}</span>
                                </div>
                                <div className="text-white font-medium">"{comm.content}"</div>
                                {comm.resolutionNote && (
                                  <div className="text-[11px] text-zinc-300 bg-[#1e1f20] p-1.5 rounded border border-[#282a2c] font-mono">
                                    💡 <strong>PM Agent 采纳</strong>: {comm.resolutionNote}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Right Sidebar: Annotation Composer & Staged Comments Drawer */}
              <div className="w-84 lg:w-96 bg-[#18191b] border-l border-[#333538] flex flex-col shrink-0 overflow-hidden">
                {/* Panel Header */}
                <div className="p-4 border-b border-[#333538] bg-[#1e1f20] flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Edit3 className="w-4 h-4 text-white" />
                    <span className="text-xs font-bold text-white">
                      批注与迭代工坊 (Annotation Hub)
                    </span>
                  </div>
                  <span className="text-[10px] px-2 py-0.5 bg-[#131314] text-zinc-300 border border-[#333538] rounded-full font-mono font-bold">
                    {stagedComments.length} 条已暂存
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                  {/* Active Commenting Composer */}
                  {activeCommentingSection ? (
                    <div className="p-3.5 rounded-xl bg-[#1e1f20] border border-white space-y-3 shadow-lg">
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-bold text-white flex items-center gap-1.5">
                          <MessageSquarePlus className="w-4 h-4 text-white" />
                          标注目标章节
                        </span>
                        <button
                          onClick={() => setActiveCommentingSection(null)}
                          className="text-zinc-400 hover:text-white text-xs cursor-pointer"
                        >
                          取消
                        </button>
                      </div>

                      <div className="p-2 bg-[#131314] rounded-lg border border-[#333538] text-[11px] font-mono text-zinc-300">
                        {activeCommentingSection.title}
                      </div>

                      {/* Quick Instruction Templates */}
                      <div className="space-y-1">
                        <span className="text-[10px] text-zinc-400 font-mono">快速建议模板:</span>
                        <div className="flex flex-wrap gap-1">
                          <button
                            onClick={() =>
                              setCommentInputText(
                                'Token 过期时间请调整为 2 小时，并补充 Refresh Token 自动无感续签规范。'
                              )
                            }
                            className="text-[10px] px-2 py-0.5 rounded bg-[#131314] text-zinc-300 hover:text-white border border-[#333538] cursor-pointer"
                          >
                            + 调整 Token TTL
                          </button>
                          <button
                            onClick={() =>
                              setCommentInputText(
                                '补充针对短信验证码 (SMS 2FA) 的备用认证降级方案与防刷频限制。'
                              )
                            }
                            className="text-[10px] px-2 py-0.5 rounded bg-[#131314] text-zinc-300 hover:text-white border border-[#333538] cursor-pointer"
                          >
                            + 增加 SMS 备用通道
                          </button>
                          <button
                            onClick={() =>
                              setCommentInputText(
                                'API 压测指标从 5,000 QPS 提升至 10,000 QPS 并增加熔断器断路规则。'
                              )
                            }
                            className="text-[10px] px-2 py-0.5 rounded bg-[#131314] text-zinc-300 hover:text-white border border-[#333538] cursor-pointer"
                          >
                            + 强化 SLA 门禁
                          </button>
                        </div>
                      </div>

                      <textarea
                        value={commentInputText}
                        onChange={(e) => setCommentInputText(e.target.value)}
                        placeholder="输入您的架构修改意见或需求变更要求..."
                        rows={4}
                        className="w-full bg-[#131314] border border-[#333538] rounded-lg p-2.5 text-xs text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-400"
                      />

                      <button
                        onClick={handleStageComment}
                        disabled={!commentInputText.trim()}
                        className="w-full py-2 bg-white hover:bg-zinc-200 disabled:bg-zinc-700 disabled:text-zinc-400 text-black text-xs font-bold rounded-lg transition-colors flex items-center justify-center gap-1.5 cursor-pointer"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        <span>暂存此条批注 (可一次暂存多条)</span>
                      </button>
                    </div>
                  ) : (
                    <div className="p-4 rounded-xl bg-[#1e1f20] border border-dashed border-[#333538] text-center space-y-2">
                      <div className="w-8 h-8 rounded-full bg-[#131314] border border-[#333538] flex items-center justify-center mx-auto text-zinc-400">
                        <MessageSquarePlus className="w-4 h-4" />
                      </div>
                      <h4 className="text-xs font-bold text-white">点击任意章节撰写批注</h4>
                      <p className="text-[11px] text-zinc-400 leading-relaxed">
                        可在左侧文档多个章节分别添加批注，批量暂存后一次性提交给 PM Agent。
                      </p>
                    </div>
                  )}

                  {/* Staged Comments List */}
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-xs font-semibold text-zinc-400">
                      <span>待提交批注列表 ({stagedComments.length})</span>
                      {stagedComments.length > 0 && (
                        <button
                          onClick={() => setStagedComments([])}
                          className="text-[10px] text-red-400 hover:underline cursor-pointer"
                        >
                          清空全部
                        </button>
                      )}
                    </div>

                    {stagedComments.length === 0 ? (
                      <div className="text-center py-6 text-xs text-zinc-500 font-mono">
                        暂无暂存批注
                      </div>
                    ) : (
                      stagedComments.map((draft, idx) => (
                        <div
                          key={draft.id}
                          className="p-3 rounded-lg bg-[#1e1f20] border border-[#333538] text-xs space-y-2 group"
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-[10px] px-1.5 py-0.2 bg-[#131314] text-zinc-300 border border-[#333538] rounded font-mono font-semibold">
                              批注 #{idx + 1} · {draft.sectionTitle.split(' ')[0]}
                            </span>
                            <button
                              onClick={() => handleRemoveStagedComment(draft.id)}
                              className="text-zinc-500 hover:text-red-400 transition-colors cursor-pointer"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                          <p className="text-white text-xs leading-relaxed font-medium">
                            {draft.content}
                          </p>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                {/* Bottom Submit Action Drawer */}
                <div className="p-4 border-t border-[#333538] bg-[#1e1f20] space-y-2">
                  <button
                    id="btn-submit-prd-annotations"
                    onClick={handleSubmitAllComments}
                    disabled={stagedComments.length === 0 || isSynthesizing}
                    className="w-full py-2.5 bg-white hover:bg-zinc-200 disabled:bg-zinc-700 disabled:text-zinc-400 text-black text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-2 cursor-pointer shadow-md"
                  >
                    <Sparkles className="w-4 h-4 text-black" />
                    <span>提交所有批注并生成新版 PRD</span>
                  </button>
                  <p className="text-[10px] text-zinc-400 text-center font-mono">
                    PM Agent 将自动综合批注，生成新版本并推送 Gitea
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* TAB 2: VERSION HISTORY & COMPLETE ANNOTATION AUDIT */}
          {activeTab === 'history' && (
            <div className="flex-1 overflow-y-auto p-6 lg:p-8 space-y-6">
              <div className="flex items-center justify-between pb-4 border-b border-[#333538]">
                <div>
                  <h2 className="text-base font-bold text-white flex items-center gap-2">
                    <History className="w-5 h-5 text-white" />
                    PRD 版本全景演进树与批注审计轨迹
                  </h2>
                  <p className="text-xs text-zinc-400 mt-1">
                    全量留存每次需求迭代的输入批注、Agent 采纳记录与 Gitea 提交快照，支持一键无损回滚。
                  </p>
                </div>
                <div className="text-xs font-mono text-zinc-300 px-3 py-1 bg-[#1e1f20] border border-[#333538] rounded-full font-semibold">
                  共 {prdVersions.length} 个历史版本
                </div>
              </div>

              {/* Version History Timeline */}
              <div className="space-y-4">
                {prdVersions.map((ver, vIdx) => {
                  const isActive = ver.version === (item.activePrdVersion || prdVersions[0]?.version);

                  return (
                    <div
                      key={ver.version}
                      className={`p-5 rounded-xl border transition-all ${
                        isActive
                          ? 'bg-[#1e1f20] border-white ring-1 ring-zinc-500'
                          : 'bg-[#18191b] border-[#333538]'
                      }`}
                    >
                      {/* Version Card Header */}
                      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
                        <div className="flex items-center gap-3">
                          <span
                            className={`text-xs font-mono font-bold px-2.5 py-0.5 rounded-full ${
                              isActive ? 'bg-white text-black' : 'bg-[#131314] text-zinc-300 border border-[#333538]'
                            }`}
                          >
                            {ver.version}
                          </span>
                          <span className="text-sm font-bold text-white">{ver.title}</span>
                          {isActive && (
                            <span className="text-[10px] text-zinc-300 font-mono px-2 py-0.2 bg-[#131314] border border-zinc-500 rounded font-semibold">
                              ● 当前生效版本
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-2 text-xs font-mono">
                          <span className="text-zinc-400">{ver.createdAt}</span>
                          <div className="flex items-center gap-1 text-[11px] px-2 py-0.5 bg-[#131314] border border-[#333538] rounded text-zinc-300">
                            <GitCommit className="w-3 h-3 text-zinc-400" />
                            <span>{ver.giteaCommit.commitHash}</span>
                          </div>

                          {!isActive && (
                            <button
                              id={`btn-rollback-${ver.version}`}
                              onClick={() => handleRollbackToVersion(ver)}
                              className="px-3 py-1 bg-white hover:bg-zinc-200 text-black text-xs font-bold rounded-md transition-colors flex items-center gap-1 cursor-pointer ml-2"
                            >
                              <RotateCcw className="w-3.5 h-3.5 text-black" />
                              <span>回滚至此版本</span>
                            </button>
                          )}
                        </div>
                      </div>

                      {/* Changelog / Summary */}
                      <div className="p-3 bg-[#131314] rounded-lg border border-[#282a2c] text-xs text-zinc-300 mb-4 leading-relaxed font-sans">
                        <strong className="text-white">迭代纪要：</strong>
                        {ver.summary}
                      </div>

                      {/* Comments tied to this version */}
                      <div>
                        <div className="text-[11px] font-mono font-semibold text-zinc-400 mb-2 uppercase tracking-wider flex items-center gap-1.5">
                          <MessageSquarePlus className="w-3.5 h-3.5" />
                          <span>本版本沉淀的评审批注与解决明细 ({ver.comments?.length || 0})</span>
                        </div>

                        {!ver.comments || ver.comments.length === 0 ? (
                          <div className="text-xs text-zinc-500 font-mono p-3 bg-[#131314] rounded border border-[#282a2c]">
                            本版本由 PM Agent 初始编排生成，无前置批注。
                          </div>
                        ) : (
                          <div className="space-y-2">
                            {ver.comments.map((comm) => (
                              <div
                                key={comm.id}
                                className="p-3 rounded-lg bg-[#131314] border border-[#333538] text-xs space-y-1.5"
                              >
                                <div className="flex items-center justify-between text-[11px] text-zinc-400">
                                  <div className="flex items-center gap-2">
                                    <span className="font-bold text-white">
                                      {comm.author.name}
                                    </span>
                                    <span className="text-[10px] px-1.5 py-0.2 bg-[#1e1f20] text-zinc-300 border border-[#333538] rounded font-mono">
                                      {comm.sectionTitle}
                                    </span>
                                  </div>
                                  <span className="font-mono text-[10px]">{comm.createdAt}</span>
                                </div>
                                <div className="text-zinc-200 font-medium pl-2 border-l-2 border-zinc-500">
                                  "{comm.content}"
                                </div>
                                {comm.resolutionNote && (
                                  <div className="text-[11px] text-zinc-300 bg-[#1e1f20] p-2 rounded border border-[#282a2c] font-mono">
                                    💡 <strong>PM Agent 采纳与修订</strong>: {comm.resolutionNote}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* 4. PM Agent Synthesizing Overlay */}
        <AnimatePresence>
          {isSynthesizing && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-6"
            >
              <div className="w-full max-w-md bg-[#1e1f20] border border-white rounded-2xl p-6 shadow-2xl space-y-4 text-center">
                <div className="w-12 h-12 rounded-2xl bg-white text-black flex items-center justify-center mx-auto shadow-md">
                  <Sparkles className="w-6 h-6 animate-spin" />
                </div>

                <div>
                  <h3 className="text-base font-bold text-white">PM Agent 正在重构 PRD 规格</h3>
                  <p className="text-xs text-zinc-400 mt-1 font-mono">{synthesisStep}</p>
                </div>

                <div className="w-full bg-[#131314] h-1.5 rounded-full overflow-hidden">
                  <div className="h-full bg-white animate-pulse w-full"></div>
                </div>

                <div className="text-[11px] text-zinc-400 font-mono">
                  Gitea Webhook 握手与 Commit 生成就绪...
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* 5. Modal Footer */}
        <div className="px-5 py-3 border-t border-[#333538] bg-[#1e1f20] flex items-center justify-between text-xs text-zinc-400 shrink-0">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-white"></span>
            <span className="font-mono text-zinc-300">GITEA DOC ENGINE V3.4 ONLINE</span>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleCopyMarkdown}
              className="px-3 py-1 bg-[#131314] hover:bg-[#282a2c] text-zinc-300 border border-[#333538] rounded-md font-mono text-xs transition-colors flex items-center gap-1 cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              <span>导出 PRD (.md)</span>
            </button>
            <button
              onClick={onClose}
              className="px-4 py-1.5 bg-white hover:bg-zinc-200 text-black font-semibold rounded-md transition-colors cursor-pointer"
            >
              完成并返回
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

