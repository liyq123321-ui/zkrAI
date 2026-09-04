# 子任务需求与实现方案 Implementation Plan

**Goal:** 每份子任务 Spec 给出对应需求原文、具体实施任务、实现方法、步骤产出及验证方式；按用户确认补齐当前项目 12 份 Spec。

**Architecture:** 在 AgentSpec JSON 中增加 implementation_plan（overview、steps），每个 step 包含 step_id、title、requirement_ids、implementation_method、expected_output、verification_method。服务从批准 PRD 提取 requirements 原文，避免模型改写需求。新输出必须有计划，历史数据可读取；现存任务经独立计划生成和整体审核后原子更新，保留原 ID、依赖及 PRD。

**Tech Stack:** Python/FastAPI/Pydantic/SQLAlchemy；React/TypeScript/Vitest。

用户追加：Spec 尽量详细，包含接口及定义。implementation_plan 增加 interface_notes、interfaces（name、kind、definition、inputs/outputs 字段 name/data_type/required/description、error_handling）、data_structures（name、definition、validation_rules）、design_decisions（decision、reason、FIXED/PROPOSED）。接口可为 HTTP、函数、事件、CLI 或文档交付契约；确实不涉及接口可空数组，但说明原因。必须区分已确认约束和建议方案，不能将未知服务、路径或批准写成事实。

## Constraints

- 仅在 zkrAI 修改；旧任务、原批准 PRD 不删除、不重新拆分。
- 计划必须覆盖该任务验收涉及的全部 FR/NFR；不得引用未知或未归属该任务的需求；步骤标识唯一、内容非空。
- 具体程度由提示词及语义审核检查：方法须描述操作、组件或接口/数据处理、异常处理与验证，不允许只是重复标题。未确认技术选择作为提案，不虚构批准、路径存在或已完成结果。
- 历史 Spec 可无计划，但新生成/补齐后必须通过完整校验。显示历史缺漏，不伪造方法。
- 本轮完成后提醒存 Git；不沿用上一轮已完成的提交请求自动推送本轮改动。

## Task 1 — 合同、校验与持久化

- [x] 为无计划、空白方法、未知需求、覆盖遗漏、重复步骤写失败测试。
- [x] 增加 ImplementationPlan/ImplementationStep 类型和验证器；历史解析兼容 None，新校验拒绝 None。
- [x] 保存批准 PRD 的原始 requirement_id/statement/priority 到 Spec requirements。
- [x] 调整完整及局部拆分提示词和 Reviewer 检查要求，验证真实 gateway 自动修补计划缺失。

## Task 2 — 任务详情展示

- [x] 增加 AgentSpecDetails，展示任务范围、需求原文、实现概述、顺序步骤、方法、产出和验证；约束/验收及原始 JSON 可展开。
- [x] 旧 Spec 可从绑定来源 PRD 显示需求，明确尚未生成实现方案；不使用当前其他版本代替来源。
- [x] 组件测试验证需求、方法可读、历史数据兼容和任务入口集成；类型检查及构建。

## Task 3 — 现有任务补齐

- [x] 增加只生成 ImplementationPlan 的 gateway 节点，复用结构修复。
- [x] 服务校验操作者及项目状态，以当前任务/PRD 快照生成计划、整体复审，最终事务再次检查快照并保存；每轮调用与更新记录审计。
- [x] 测试成功保存、失败无部分写入、并发变化拦截及现有 ID/依赖/PRD 不变。
- [x] 在授权会话生成并复审实际 12 份计划，统一保存后刷新浏览器查看。最终审核 PASS，共 93 个实施步骤、37 个接口定义、91 个数据结构定义；状态版本 20。

## Task 4 — 验证与交付

- [x] 完整后端和前端测试、类型检查、构建；代码复核。后端 539 项、前端 44 项通过。
- [x] 更新修改日志、加载新后端，核对真实页面及数据。历史任务与新方案均可读取；批准 PRD、任务身份和依赖未变化，接口和实施步骤已在真实页面核对。

用户已明确授权通过该 AI 系统现有 Agent 调用链处理 PRD 与任务 Spec，并在审核后写回本机。实际补齐已完成；原始数据已在本机备份，独立审核调用为 `5cf83d50-7cd2-4f66-86ba-867f28b3148e`。
