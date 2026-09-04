# PRD 澄清门禁界面修复设计

## 背景

当自动审核将当前 Spec 标记为 `NEED_CLARIFICATION` 时，后端会按既有工作流关闭 Gitea PRD 审核写入，只允许用户先提交澄清或显式跳过澄清。当前前端仍将 Root 卡片标记为“打开 PRD 审核”，并并发请求正文、版本、Diff、可批注行和评论。对于尚未建立 Gitea 绑定的新 Spec，这些请求返回 `PRD_REVIEW_CLOSED`，前端继而将预期门禁误报为“批注或审核数据暂不可用”。

## 目标

- `NEED_CLARIFICATION` 时仍可查看数据库中已保存的 PRD 正文。
- 不把预期的工作流门禁显示为服务异常。
- 明确告知用户：提交澄清并进入 `REWORK` 后，Diff 与批注才会开放。
- 不改变后端审核状态机、权限规则或 Gitea 数据。

## 界面行为

1. Root 卡片在当前 Spec 为 `NEED_CLARIFICATION` 时显示“查看 PRD / 回答澄清”，其他已生成 Spec 继续显示“打开 PRD 审核”。
2. 打开弹窗后，`PrdReviewPanel` 检测到 `NEED_CLARIFICATION`，不调用 PRD review 的五个 Gitea 读取接口。
3. 弹窗以 `fallbackSpec.markdown` 展示已保存正文，并显示专用提示：当前 PRD 正在等待澄清；请在左侧提交答案，进入返工阶段后即可查看 Diff 和添加批注。
4. Diff、行选择、批注提交、评论回复和审核发布保持不可用；不提供绕过状态机的操作。
5. 其他状态保持现有加载、降级与错误处理行为。

## 代码边界

- 在 `workflowUi.ts` 增加一个纯函数，用当前 Spec 状态生成 Root 卡片文案，避免卡片组件内散落状态判断。
- 在 `PrdReviewPanel.tsx` 增加澄清门禁分支，复用现有正文降级数据，不触发网络请求。
- `ApiWorkspace.tsx` 将所属项目的 `current_spec_status` 传给卡片文案函数；不改变弹窗选择和项目归属逻辑。

## 错误处理

专用澄清提示不是错误，不使用 `PRD_REVIEW_CLOSED` 或“暂不可用”措辞。非澄清状态下的网络、Gitea 权限和版本冲突仍走现有错误路径。

## 测试

- 纯函数测试覆盖 `NEED_CLARIFICATION` 与普通 Root 状态的卡片文案。
- 组件测试覆盖等待澄清时：正文可见、专用提示可见、PRD review 接口没有被请求、Diff 与批注控件不可用。
- 运行前端相关测试与完整前端测试，确认现有人工审核流程不受影响。
