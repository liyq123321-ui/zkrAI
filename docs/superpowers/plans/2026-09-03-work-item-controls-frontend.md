# WorkItem 前端交互 Implementation Plan

> **For agentic workers:** Use executing-plans to implement this bounded frontend change in the current session; no parallel implementation is needed.

**Goal:** 子任务详情提供未接入执行流程的对话入口，以及可在本页预览的员工选择。

**Architecture:** ApiWorkspace 按 Session/WorkItem 保存页面内存状态。AgentSpecDetail 接收状态、员工列表和修改回调；WorkItemAgentPanel 只展示待接入对话和编辑草稿。employeeDirectory 管理 EmployeeOption 与演示名单。

**Tech Stack:** React 19、TypeScript、现有 CSS、Vitest / Testing Library。

## Global Constraints

- 仅修改前端代码与需求/说明文档；无新增 API、后端、数据库和真实 Agent 调用。
- 刷新清空本页草稿与指派预览；关闭详情后重开保留。
- 禁用发送/中断，演示员工标注示例；保持现有任务状态和规格。
- 不自动提交或推送；完成提醒保存 Git。

## Task 1: 页面状态与交互

**Files:**
- Create: `frontend/aios-main/src/api/employeeDirectory.ts`
- Create: `frontend/aios-main/src/api/WorkItemAgentPanel.tsx`
- Modify: `frontend/aios-main/src/api/AgentSpecDetail.tsx`
- Modify: `frontend/aios-main/src/api/ApiWorkspace.tsx`
- Modify: `frontend/aios-main/src/index.css`
- Test: `frontend/aios-main/src/api/workspace.test.tsx`

**Interfaces:**
```ts
interface EmployeeOption { id: string; name: string; role?: string }
interface WorkItemPreview { draft: string; assigneeId?: string }
// Empty assigneeId means explicitly unassigned; undefined uses original assignee.
```

- [x] 添加用户路径测试：打开任务、填写草稿、改变员工、关闭、切换任务并重开；验证草稿隔离与卡片姓名同步。
- [x] 测试输入快捷填入不覆盖已有内容、发送/中断禁用及预览不发请求；清空指派不回退旧值，重新挂载清空预览。
- [x] 运行 `npm test -- src/api/workspace.test.tsx`，确认新功能测试先失败。
- [x] 实现独立名单、受控对话区与页面内存状态；由 `onPreviewChange(patch: Partial<WorkItemPreview>)` 更新当前任务，使用显示副本渲染负责人，不修改 resources。
- [x] 补充现有详情 CSS，完成宽屏和窄屏布局。
- [x] 运行同一测试确认通过，再运行 `npm test`、`npm run lint`、`npm run build`。

## Task 2: 说明与浏览器验收

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md`
- Modify: `docs/superpowers/specs/2026-09-03-work-item-control-design.md`

- [x] 文档写清对话未接入、示例名单、本页状态生命周期与后续接口接入位置。
- [x] 使用浏览器打开实际子任务，验证草稿与指派预览，检查窄屏布局；刷新后恢复原值。
- [x] 核对 `git diff --check` 与修改范围，确认 backend 无改动。
- [x] 完成代码复核、更新计划完成状态并交付，提醒存 Git。

## 验证结果

2026-09-03：前端 50 项测试、TypeScript 类型检查与生产构建通过；已在实际任务上检查对话草稿、员工选择、卡片同步、关闭重开及 390px 窄屏显示。代码复核无阻塞问题，backend 无改动。
