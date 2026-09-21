# firstFlight 工程文档索引

> 最后更新：2026-09-15。当前源码和测试是最终事实来源；历史文档仅作背景参考。

## 可独立迁移的模块文档

以下三份文档彼此独立，可随对应代码单独交给 AI 或迁移到其他工程：

| 模块 | 文档 | 页面入口 | 前端入口 |
| --- | --- | --- | --- |
| Dashboard | [`dashboard.md`](dashboard.md) | 主工作台 Dashboard 标签 | `frontend/aios-main/src/api/WorkspaceDashboard.tsx` |
| 工作空间 | [`workspace.md`](workspace.md) | `/workspace` | `frontend/aios-main/src/api/WorkspacePortal.tsx` |
| Block PRD | [`block-prd.md`](block-prd.md) | `/prd-editor/:sessionId` | `frontend/aios-main/src/api/prd-editor/PRDEditor.tsx` |

Block PRD 的完整用户行为、运行限制和历次验证记录另见 [`block-prd-editor.md`](block-prd-editor.md)。

## 其他当前说明

- [`architecture/frontend-codex-architecture-and-api.md`](architecture/frontend-codex-architecture-and-api.md)：主前端架构与 API。
- [`backend/README.md`](backend/README.md)：后端运行方式与 API 示例。
- [`backend/gitea-prd-review-engineering-guide.md`](backend/gitea-prd-review-engineering-guide.md)：后端工程框架与 Gitea PRD 审核链路。
- [`frontend-backend-integration-contract.md`](frontend-backend-integration-contract.md)：第一阶段前后端契约。
- [`frontend-backend-integration-tasks.md`](frontend-backend-integration-tasks.md)：第一阶段集成任务记录。
- [`local-deployment.md`](local-deployment.md)：本机部署记录。
- [`sdlc/README.md`](sdlc/README.md)：生命周期规划规则入口。
- `openapi.json`：接口快照；使用前应与运行中的 `/openapi.json` 比较。

## 调研、计划与历史资料

- [`工程解析总览.md`](工程解析总览.md)：2026-09-03 工程快照，路径和行号可能过时。
- [`图引擎调研.md`](图引擎调研.md) 与 [`任务执行引擎实施计划.md`](任务执行引擎实施计划.md)。
- `superpowers/specs/`、`superpowers/plans/`：历次设计与实施计划。
- [`archive/old-frontend-codex-architecture-and-api.md`](archive/old-frontend-codex-architecture-and-api.md)：旧前端快照，仅用于迁移对照。

## 已整理的路径

| 原路径 | 新路径 |
| --- | --- |
| `frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md` | `docs/architecture/frontend-codex-architecture-and-api.md` |
| `old_frontend/aios-main/CODEX_ARCHITECTURE_AND_API_SPEC.md` | `docs/archive/old-frontend-codex-architecture-and-api.md` |
| `backend/readme.md` | `docs/backend/README.md` |
| `backend/gitea-prd-review-engineering-guide.md` | `docs/backend/gitea-prd-review-engineering-guide.md` |
| `backend/docs/superpowers/{plans,specs}/` | `docs/superpowers/{plans,specs}/` |

根目录 `README.md`、`AGENTS.md`、`CHANGELOG.md` 保留原位。示例 PRD、生成结果、临时预览和技能包自带文档不属于工程说明归档范围，未移动。
