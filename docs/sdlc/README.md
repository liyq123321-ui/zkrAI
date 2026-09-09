# SDLC 生命周期规划总纲

本规则由用户提供的《SDLC生命周期模型选择与执行SOP》转写，约束 firstFlight
在**需求澄清、PRD 获批之后**生成的生命周期阶段、里程碑和任务。来源原文逐字保存在
[source.md](source.md)，内容哈希记录于选择规则。原文中的 Agent 指令是转换素材，
不是对当前会话额外授权。

## 文件与职责

| 文件 | 用途 |
| --- | --- |
| `backend/rules/sdlc/selection.yaml` | 10 项选择题、阈值、未知和冲突处理、规划约束 |
| `backend/rules/sdlc/models/strict_waterfall.yaml` | 纯线性瀑布：6 个正式阶段 |
| `backend/rules/sdlc/models/overlapping_waterfall.yaml` | 阶段重叠瀑布：5 个核心阶段 |
| `backend/rules/sdlc/models/iterative_incremental.yaml` | 一次启动、每轮 4 阶段、贯穿运维 |
| `backend/rules/sdlc/execution.yaml` | 依赖策略、允许重叠、分级评审、缺陷与变更响应 |
| `backend/app/domain/sdlc.py` | 严格结构化输出模型；禁止额外字段 |
| `backend/app/services/sdlc_rules.py` | YAML 加载、路线选择、阶段与任务 DAG 校验 |
| `backend/scripts/sdlc.py` | 离线选择与计划校验 CLI |
| `lifecycle.schema.json` | 从同一 Pydantic 模型导出的 JSON Schema |
| `selection.template.yaml` | 未回答的选择题模板；未知时不可直接作为正式计划 |
| `examples/*.json` | 三种路线的完整合成示例，供格式参考与离线校验 |
| `decisions.md` | 原文冲突、工程补充与适用边界 |

模型 YAML 保留原始条款 `source_sections`，同时把每个阶段的活动与交付物拆成
稳定 ID。`execution.yaml` 中有明确目标和动作的缺陷条款与变更策略，供后续处理
调用；当前程序自动执行的是选择与**规划校验**，并不执行生产回滚或代替人工审批。

## 自动规划入口

1. 澄清 Agent 收集影响路线的业务事实，并写回 Brief。PRD 生成与重写保留这些事实、
   未决问题和假设。不会为凑选型分数猜测变更率或团队人数。
2. `DecompositionService.prepare()` 只从已批准 PRD 生成任务，在基础拆分调用中注入
   服务端加载的 YAML 规则、版本及内容哈希。这里选择的是软件交付生命周期，
   不替换应用已有的 `ProjectPhase`/`SpecStatus` 状态机。
3. PM 返回 `WorkBreakdown.lifecycle`：10 个选择答案与原文证据、选型理由、
   阶段实例、任务映射、排期和运维交接安排。后端重新计算选型，不信任模型自报结果。
4. 基础拆分先校验阶段与依赖，再进行逐任务详细规划和独立 Reviewer 审核。
   逐任务规划、修订和 Reviewer 都收到 SDLC 规则。Reviewer 检查证据含义、
   实际范围、职责、工期可行性和原文条款，弥补纯结构校验不能判断的语义。
5. 直接写入与两阶段命令的 materialize 边界再次校验。所有新拆分必须提供 SDLC，
   缺失、规则过期、阶段遗漏、门禁缺失都不能写入 WorkItem/AgentSpec。
6. AgentSpec 的 `content.sdlc` 保存规则哈希、路线、阶段、任务映射和选定模型规则。
   原始选择证据与完整计划保留在持久化 AgentCall 中。任务依赖照常写入数据库，
   现有执行 DAG 因此能阻止上游门禁任务完成前启动下游任务。
7. 用户确认路线后，前端总页面的“顶层图”Tab 立即从路线决策显示自上而下的阶段，
   并把第一阶段标记为当前阶段。完成任务拆分后切换到 `content.sdlc` 与真实 MILESTONE；
   阶段状态来自该阶段下的任务和准出门禁。点击阶段卡片打开独立阶段详情，查看该阶段
   的子任务分层、依赖关系、分配 Agent 和运行状态；顶层图本身只呈现 SDLC 阶段路线。
   左侧监控区汇总 Codex 公开事件中的 Token、Agent 调用、人员分配和阶段完成度。

旧记录仍能读取；旧检查点不能用于绕过新规则。拆分合同版本已从 5 升到 6，
重用检查点还要匹配规则哈希与任务生命周期上下文。规则在运行中变化时会阻止
旧方案写入，需要重新规划。既有已拆分项目不会被自动改写或重新执行。

## 如何拆分

- 纯线性瀑布：需求定审 → 设计签审 → 编码实现 → 测试验收 → 部署交付 → 运维归档。
- 阶段重叠瀑布：需求锁定 → 架构设计 → 编码测试 → 集成验收 → 运维迭代。
- 迭代增量：全局待办构建一次；每轮迭代规划 → 设计开发 → 验证演示 → 发布反馈；
  持续运维贯穿。全局/持续阶段的 `iteration=0`；迭代从 1 连续编号。

每个阶段实例对应一个 MILESTONE，标题必须与 YAML 完全一致。迭代编号保存在
`lifecycle.phases[].iteration`，不拼进四字阶段标题。每个 TASK 通过 `parent_key`
归属阶段，用 `activity_ids` 与 `deliverable_ids` 覆盖工作和交付物；交付物名称还须
出现在 Agent Spec 的必需输出中。任务保留现有 FR/NFR 追溯、责任人、输入、验收与
具体步骤，不用阶段名称代替任务细节。

每阶段用 `exit_gate_key` 指定一个准出评审 TASK，它依赖该阶段全部其他任务。
将 `required_gate_checks` 原样写入其 `gate_checks` 和验收条款，排定证据检查及
相应角色评审。门禁任务的计划不等于门禁已通过；不得伪造签字、测试结果、发布或反馈。

纯瀑布后续任务必须传递依赖上阶段准出门禁。重叠瀑布可以保守串行，也可使用
`core_requirements_review`、`architecture_review`、`module_design_review` 专项门禁。
提前启动必须依赖已产出的核心/模块交付物与相应评审任务，编码只能使用同模块设计；
非相邻阶段不能重叠，全系统验收仍须等全部模块完成。分开的模块测试任务依赖同模块编码。

迭代内禁止跳阶段。下一轮预规划可在上一轮开发准出后开始，但下一轮规划的正式
准出要等上一轮验证验收。持续运维只依赖启动门禁，不等待末轮发布；用有限的交接
任务和持续服务约定表达长期运维，不创建永远无法完成的全局阻塞节点。

排期比例保留原文建议，不能相加后机械认定为 100%；重叠时长与持续运维尤其不能
混算。计划必须给出每阶段排期及运维交接安排；实际容量、工作量与日期由 PM 和
Reviewer 审核，不由结构校验器声称已经验证。

## 离线使用与验证

在项目根目录执行；依赖使用后端现有环境，增加了 `PyYAML>=6.0.3,<7`：

```powershell
& backend/.venv/Scripts/python.exe backend/scripts/sdlc.py catalogue
& backend/.venv/Scripts/python.exe backend/scripts/sdlc.py select docs/sdlc/selection.template.yaml
& backend/.venv/Scripts/python.exe backend/scripts/sdlc.py validate docs/sdlc/examples/strict_waterfall.json
& backend/.venv/Scripts/python.exe backend/scripts/sdlc.py schema
```

模板全部为 unknown，所以第二条命令预期以退出码 2 报告需要澄清；填写确认答案后重试。
`select` 只检查选型逻辑；`validate` 还检查证据引用、阶段、任务、交付物、门禁与 DAG。
JSON 和 YAML 均可输入。退出码 0 表示结构检查通过，2 表示需要修正或澄清。
YAML 中的 `yes`、`no` 请加引号，以保持字符串类型。
离线校验接受基础拆分中尚未生成的详细计划；服务端仍要求全部详细计划和 Reviewer 通过。

```powershell
cd backend
& .venv/Scripts/python.exe -m pytest tests/unit/test_sdlc_rules.py tests/integration/test_sdlc_decomposition.py -q -p no:cacheprovider
```

例子明确包含合成证据，不代表当前项目选型、实际交付或签字。不要把示例数据复制到真实 PRD。
规则或输出模型修改后，运行 `backend/scripts/export_sdlc_examples.py` 同步 Schema 与合成示例。
后端部署进程需要重启才能加载本次 Python/提示词变更；此修改不包含自动重启或生产发布。

## 严格遵循的边界

已自动强制：选择阈值与冲突处理、证据引用存在、规则版本、阶段体系、任务映射、
活动和交付物覆盖、准出检查入验收条款、DAG 门禁、允许重叠与落库前复查。

仍须真实人员/外部系统提供：证据的业务真实性、正式签字、审批会记录、覆盖率阈值、
生产发布与回滚结果、稳定运行时长、有效用户反馈。现有执行 API 的“完成任务”不具备
电子签章验证能力；本次没有把它宣称为合规审批系统。缺陷/变更规则供规划和处理方案
引用，实际执行动作仍受项目授权与执行系统能力约束。
