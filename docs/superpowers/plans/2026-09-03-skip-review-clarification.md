# 跳过 PRD 澄清并拆分子任务

> 按用户已确认的流程执行：在“提交澄清”下增加按钮，直接确认当前 PRD 并拆分子任务。

**目标：** 复用 skip_clarification 和 convert_to_work_item，在 REVIEW / NEED_CLARIFICATION 阶段支持显式人工确认当前版本并继续拆解。

**约束：** 保留现有未提交修改；初始澄清跳过仍生成 PRD。当前 PRD 内容及历史审核意见不被改写。普通文字“跳过澄清”不隐式批准已有 PRD。无需再次询问确认；按钮本身说明确认动作。

- [x] 后端：为 REVIEW / NEED_CLARIFICATION 增加 skip_clarification。已有 PRD 时要求 payload.confirm_current_spec=true 和 payload.spec_version_id 与当前版本一致；验证操作人及状态版本。在同一事务保存跳过回复、HUMAN/PASS 审核、审计及幂等回执，并置 APPROVED，提供 convert_to_work_item。
- [x] 后端测试：复现原接口不允许跳过；覆盖确认及拆解成功、重放不重复、权限、缺失确认、错误版本与过期状态；原始 PRD 内容保持不变。
- [x] 前端：按钮位于提交澄清下方，按当前是否有 PRD 使用不同文案与 payload。已有 PRD 跳过成功后自动 convert_to_work_item；首次澄清维持 create_spec。执行中禁用重复操作；失败仍可按保存的 APPROVED 状态继续拆解。
- [x] 前端测试：真实组件点击后请求顺序为 skip_clarification → convert_to_work_item，携带返回的新状态版本；不调用生成或普通 approve；测试原始澄清流程、普通跳过文本、失败恢复。
- [x] 验证与加载：后端测试、前端测试/类型检查/构建；复核改动；重启 zkrAI 后端并验证健康、刷新本地前端静态资源，用浏览器核对真实按钮（不点击用户真实项目的确认按钮）。
- [x] 完成后提醒用户存 Git，不代为提交混合工作区。

涉及文件：backend/app/domain/workflow.py、backend/app/services/command_service.py、backend/tests/integration/test_skip_review_clarification.py、backend/tests/unit/test_workflow_policy.py、frontend/aios-main/src/api/ApiWorkspace.tsx、frontend/aios-main/src/api/workspace.test.tsx，以及必要的流程说明。

验证结果：后端 406 项、前端 31 项测试通过；TypeScript 与 Vite 构建通过。独立审查及重试入口复查通过。已重启 local.zkrai.backend 和 zkrai-web，健康检查 status=ok/database=ok；原问题会话的 legal_actions 已包含 skip_clarification。浏览器确认新按钮紧接提交澄清；未点击实际项目的确认按钮。
