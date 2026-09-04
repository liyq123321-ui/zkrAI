# 拆解必需输出规范化设计

## 背景

温度换算器项目的 `decompose_spec` 连续三次生成了可解析的 `WorkBreakdown`，但任务 `T1` 的输出定义全部为 `required=false`。现有校验要求每个任务至少有一个必需交付物，因此最终以 `MISSING_OUTPUT` 拒绝结果并向前端返回 `INVALID_AGENT_RESULT`。

## 方案选择

采用确定性规范化：对于 `WorkBreakdown` 或 `WorkBreakdownRevision` 中输出列表非空、但没有任何 `required=true` 的 Agent Spec，将列表中的第一个既有输出标记为必需，然后继续执行现有完整校验。

不采用仅加强提示词的方案，因为当前提示和两轮修复诊断已经明确要求必需输出，仍连续失败。不采用删除 `MISSING_OUTPUT` 校验的方案，因为那会允许没有明确交付物的任务进入执行阶段。

## 数据流与边界

规范化发生在 Pydantic 成功解析之后、`validate_breakdown` 之前，与现有的唯一近似 `context_ref` 纠错位于同一输出规范化层。它只修改布尔标记，不新增、删除、改名或重排输出，也不改变任务、依赖、范围、验收标准或来源引用。

以下情况不自动修复：

- `outputs` 为空或无法通过 schema 解析；
- 输出名称或格式为空；
- 已经存在至少一个 `required=true`；
- 其他拆解契约错误。

`WorkBreakdownRevision` 使用相同规则，使后续语义修订不会再次因同一布尔标记失败；未出现在 revision 中的 Agent Spec 保持原样。

## 错误处理

规范化后始终运行现有 `validate_breakdown`。若仍存在空输出、非法字段、缺失 Agent Spec、错误依赖或其他契约问题，继续进入最多三次的 Agent 修复流程，最终仍可返回 `INVALID_AGENT_RESULT`，不降低校验门槛。

## 测试与验收

- 初始拆解中输出均为非必需时，一次 Agent 响应即可规范化并通过校验。
- revision 中输出均为非必需时，同样规范化并正确合并。
- 已有必需输出的有效结果保持不变。
- 空输出列表仍走 schema 修复预算，并在三次均无效后失败。
- 运行输出修复单元测试和完整后端测试集。

