from __future__ import annotations

import re
from pathlib import Path

from docx import Document

from build_mindmap_v15 import article_panel, build_article


ROOT = Path(r"C:\Users\zkr\Documents\ChatGPT\firstFlight Project")
SOURCE_HTML = ROOT / "output" / "firstflight_mindmap_v15.html"
OUTPUT_HTML = ROOT / "output" / "firstflight_mindmap_v16.html"
SOURCE_DOCX = Path(
    r"C:\Users\zkr\Documents\WXWork\1688857625913676\Cache\File\2026-09\人机混动软件工程平台 PRD（v0.1） (10).docx"
)


V16_CSS = r"""
  /* v16：节点卡片直接跳转最新版 PRD；侧栏打开后仍保留可操作的思维导图区。 */
  #articlePanel{width:min(720px,60vw);}
  .node .card{padding-right:53px;}
  .node-doc-jump{position:absolute;right:6px;top:6px;z-index:3;border:1px solid #3d4856;border-radius:6px;
    background:#0d1117e8;color:#a5d6ff;padding:3px 6px;font:600 9px/1.2 system-ui,-apple-system,"Segoe UI",sans-serif;
    letter-spacing:.1px;cursor:pointer;opacity:.82;box-shadow:0 1px 4px rgba(0,0,0,.28);}
  .node-doc-jump:hover,.node-doc-jump:focus-visible{opacity:1;color:#fff;background:#1f6feb;border-color:#58a6ff;outline:none;}
  .node-doc-jump:active{transform:translateY(1px);}
  body.prd-article-open #articlePanel{box-shadow:-18px 0 42px rgba(0,0,0,.5);}
  @media (max-width:900px){#articlePanel{width:100%;}.node-doc-jump{opacity:1;}}
"""


ARTICLE_JS = r"""
/* ---- v16 最新 PRD 连续文章侧栏与节点直达定位 ---- */
const NODE_PRD_SECTION = {
  root:"人机混动软件工程平台 PRD",
  "domain-organization":"FR-1.5 空间管理",
  "domain-requirements":"背景与用户问题",
  "domain-planning":"执行图编排（Harness 工程）",
  "domain-execution":"FR-2.2 图执行驱动",
  "domain-quality":"审查与确认机制",
  "domain-collaboration":"FR-8.5 个人工作台",
  "domain-platform-ops":"产品总框架",
  "platform-positioning":"产品目标",
  "tenant-identity":"FR-1.6 身份接入与角色体系",
  "org-role":"FR-1.12 组织架构管理",
  "rbac-scope":"FR-1.6 身份接入与角色体系",
  "avatar-team":"FR-1.1 员工与 Agent 分身",
  "space-lifecycle":"FR-1.5 空间管理",
  "identity-accept":"FR-1.6 身份接入与角色体系",
  s0:"背景与用户问题", s1:"背景与用户问题", s2:"内容创作与变更存档", s3:"审查与确认机制",
  "content-creation":"FR-1.13 自然语言画图与文档编辑",
  "product-domain-model":"领域模型（核心 ER）",
  "template-compile":"FR-1.9 模板管理",
  "graph-definition":"FR-2.1 图定义",
  "harness-control":"FR-2.2 图执行驱动",
  "node-runtime":"FR-2.4 节点生命周期与运行子图",
  s4:"FR-2.4 节点生命周期与运行子图",
  "sdlc-hub":"优先级与里程碑",
  "automation-switches":"FR-4.5 确认负担与分层确认",
  "capability-tool":"FR-1.8 能力与工具管理逻辑",
  "model-management":"FR-1.22 模型管理",
  "skill-hub":"FR-1.4 持续学习与技能蒸馏",
  "agent-config-scan":"Agent 定义与配置自扫",
  s5:"研发主链路：代码工作流集成方案",
  "sandbox-codeflow":"研发主链路：代码工作流集成方案",
  "code-hub":"一次代码工作流闭环",
  "capability-accept":"FR-1.21 能力与工具引入校验与评估",
  "review-confirmation":"FR-4.1 Agent 自查与条目化审查报告",
  "release-human-gate":"FR-4.5 确认负担与分层确认",
  "dual-hub":"FR-4.3 复审机制",
  "issue-management":"FR-3.5 问题管理",
  "data-hub":"系统架构与领域模型",
  "graph-accept":"FR-2.5 图观测",
  "accept-hub":"审查与确认机制",
  s6:"一次代码工作流闭环",
  "kill-criteria":"Kill Criteria",
  "personal-workbench":"FR-8.5 个人工作台",
  "operator-actions":"FR-8.5 个人工作台",
  "message-todo":"FR-8.2 待办模式",
  "workitem-kanban":"FR-3.3 看板",
  "product-observation":"FR-5.1 项目→节点逐层展开",
  "product-metrics":"FR-5.4 项目详情画布",
  "collab-accept":"FR-8.5 个人工作台",
  "product-ia":"产品总框架",
  "product-status-contract":"核心概念模型",
  "product-glossary":"核心概念模型",
  "ws-hub":"FR-1.5 空间管理",
  "ws-company":"企业空间",
  "ws-project":"协作空间",
  "ws-personal":"个体空间",
  "ws-knowledge":"FR-1.10 领域知识管理",
  "ws-skills":"FR-1.4 持续学习与技能蒸馏",
  "knowledge-memory":"FR-1.11 长短期记忆与使用环节",
  "plugin-knowledge":"FR-1.18 插件框架与接入范式",
  "execution-log":"日志细节要求",
  "nfr-security":"安全边界初稿",
  "m0-architecture":"优先级与里程碑",
  "billing-roadmap":"计费模型",
  "hub-cross":"系统架构与领域模型"
};
const articlePanel=document.getElementById("articlePanel");
const articleScroll=document.getElementById("articleScroll");
const articleHeadings=[...document.querySelectorAll("#prdArticle [data-prd-heading]")];
function normalizedArticleText(value){return String(value||"").toLowerCase().replace(/[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳\s·｜|（）()【】\[\]：:、，。,.\/\\_-]/g,"");}
function headingByHint(hint){
  const wanted=normalizedArticleText(hint);
  if(!wanted) return null;
  return articleHeadings.find(h=>normalizedArticleText(h.dataset.headingText).includes(wanted)||wanted.includes(normalizedArticleText(h.dataset.headingText)))||null;
}
function headingByFr(code){
  const normalized=String(code||"").toUpperCase();
  return articleHeadings.find(h=>(h.dataset.fr||"").toUpperCase().split(",").includes(normalized))||null;
}
function resolvePrdHeading(n){
  let current=n;
  while(current){
    if(NODE_PRD_SECTION[current.id]){const hit=headingByHint(NODE_PRD_SECTION[current.id]);if(hit)return hit;}
    const text=`${current.title||""} ${current.desc||""} ${current.src||""}`;
    const refs=[...text.matchAll(/FR-\d+(?:\.\d+)?/gi)].map(m=>m[0]);
    for(const ref of refs){const hit=headingByFr(ref);if(hit)return hit;}
    current=current.parent;
  }
  const title=normalizedArticleText(n?.title||"");
  let best=null,bestScore=0;
  articleHeadings.forEach(h=>{
    const heading=normalizedArticleText(h.dataset.headingText);
    let score=0;
    for(let i=0;i<title.length-1;i++){const pair=title.slice(i,i+2);if(heading.includes(pair))score++;}
    if(score>bestScore){best=h;bestScore=score;}
  });
  return bestScore>=2?best:document.getElementById("prd-title");
}
function markArticleTarget(target){
  document.querySelectorAll(".article-target,.article-search-hit").forEach(el=>el.classList.remove("article-target","article-search-hit"));
  target.classList.add("article-target");
  target.scrollIntoView({behavior:"smooth",block:"start"});
  document.getElementById("articleLocation").textContent=`定位：${target.dataset.headingText||target.textContent.trim().slice(0,60)}`;
  document.querySelectorAll(".article-toc button").forEach(btn=>btn.classList.toggle("active",btn.dataset.articleTarget===target.id));
}
function openPrdArticle(n){
  articlePanel.classList.add("open");articlePanel.setAttribute("aria-hidden","false");document.body.classList.add("prd-article-open");
  const target=n?resolvePrdHeading(n):document.getElementById("prd-title");
  requestAnimationFrame(()=>markArticleTarget(target||document.getElementById("prd-title")));
}
function closePrdArticle(){articlePanel.classList.remove("open");articlePanel.setAttribute("aria-hidden","true");document.body.classList.remove("prd-article-open");}
document.getElementById("btnArticle").addEventListener("click",()=>openPrdArticle(state.selected?nodeMap[state.selected]:null));
document.getElementById("pArticleOpen").addEventListener("click",()=>openPrdArticle(nodeMap[activeNodeEditId]||null));
document.getElementById("articleClose").addEventListener("click",closePrdArticle);
document.getElementById("articleBack").addEventListener("click",closePrdArticle);
document.querySelectorAll(".article-toc button").forEach(btn=>btn.addEventListener("click",()=>{const target=document.getElementById(btn.dataset.articleTarget);if(target)markArticleTarget(target);}));
function articleSearch(){
  const query=document.getElementById("articleSearchInput").value.trim().toLowerCase();
  if(!query)return;
  const candidates=[...document.querySelectorAll("#prdArticle h2,#prdArticle h3,#prdArticle h4,#prdArticle h5,#prdArticle p,#prdArticle td")];
  const target=candidates.find(el=>el.textContent.toLowerCase().includes(query));
  if(!target){document.getElementById("articleLocation").textContent=`未找到：${query}`;return;}
  document.querySelectorAll(".article-search-hit").forEach(el=>el.classList.remove("article-search-hit"));
  target.classList.add("article-search-hit");target.scrollIntoView({behavior:"smooth",block:"center"});
  document.getElementById("articleLocation").textContent=`搜索命中：${target.textContent.trim().slice(0,60)}`;
}
document.getElementById("articleSearchBtn").addEventListener("click",articleSearch);
document.getElementById("articleSearchInput").addEventListener("keydown",event=>{if(event.key==="Enter")articleSearch();});
document.addEventListener("keydown",event=>{if(event.key==="Escape"&&articlePanel.classList.contains("open")){closePrdArticle();event.stopPropagation();}});
"""


V16_CONTENT_JS = r"""
/* v16：按最新版 PRD (10) 的业务位置更新，不按“是否实现”集中归档。 */
(function applyLatestPrdV10(){
  const get=id=>ORIGINAL_NODE_BY_ID[id];
  const add=(parent,node)=>{if(parent&&!parent.children.some(child=>child.id===node.id))parent.children.push(node);};
  const replaceChild=(node,needle,title,src)=>{
    if(!node)return;
    const hit=node.children.find(child=>String(child.title||"").includes(needle));
    if(hit){hit.title=title;if(src)hit.src=src;}
  };

  TREE.title="firstFlight 工作进展全景 · v16";

  const foundation=get("product-foundation");
  if(foundation) foundation.desc="最新版 PRD 以 Harness 工程作为控制面/协同面范式：执行图是当前规划形态，coding agent 是可替换的黑盒 Worker；产品口径、实现证据和待决策项继续分层表达。";
  replaceChild(get("product-ia"),"L0 底座与基本概念","L0 底座与基本概念：空间、Harness/执行图、Work Item、能力工具、三层记忆与沙箱","最新版 PRD L0");
  replaceChild(get("product-glossary"),"Skill：","Skill：可复用工作流与知识包；FR-1.4 升级为持续学习与技能蒸馏，仍以真实数据、评估和人类校验为前提","最新版 PRD FR-1.4");

  const automation=get("automation-switches");
  if(automation){
    automation.title="② 人工介入分层与不可关闭发布门";
    automation.children=[
      pi("项目全局开关控制节点内规划/执行/检查三阶段是否安排人类介入，节点可覆盖；所有变更留痕","最新版 PRD FR-2.3 / FR-4.5"),
      pi("节点出口可由独立 Agent 预审/互审通过；allow_agent_review 决定结论能否自动放行或仍需 owner 确认","最新版 PRD FR-4.4 / FR-4.5"),
      pi("auto_confirm 只放开节点内闸门，不能解释为放开最终产品交付与发布责任","最新版 PRD 责任边界"),
      pi("最终产品交付发布必须经过人类审核；软件研发域固定为技术评审 + CAB，任何项目或节点开关不可关闭","最新版 PRD 人类审核铁律"),
    ];
  }

  const review=get("review-confirmation");
  if(review){
    review.children.push(
      pi("reviewer ≠ executor：检查者不得复用产出方上下文；至少全新上下文独立评判，完整档采用跨模型/跨 Worker 互审","最新版 PRD FR-4.1"),
      pi("检查条目与验收原文默认 locked=true、mutable_by=human_only；Worker 只填 result/evidence，不得改控制面条目","最新版 PRD FR-1.9 / FR-4.1"),
      pi("post-hook 对 checklist/acceptance 做 hash diff；发现执行期篡改立即阻断、登记 Issue 并告警","最新版 PRD FR-4.1"),
    );
  }

  const template=get("template-compile");
  if(template) template.children.push(
    pi("检查条目模板默认锁定；定义权归领域管理员/owner，审查 Agent 与执行 Worker 只能分别填写结论和证据","最新版 PRD FR-1.9 Schema")
  );

  const knowledge=get("knowledge-memory");
  if(knowledge){
    knowledge.title="④ 业务知识 · 技术栈规则 · 三层记忆（WeKnora 已接入）";
    knowledge.children=[
      pi("业务领域知识承载行业名词、系统与业务基础信息；v0.1 最小灌入集优先覆盖业务侧","最新版 PRD FR-1.10"),
      pi("技术栈规则包与业务知识并列：通用安全/风格/Git/测试规则 + 语言框架惯用法，可安装、版本化、自动匹配 pre-hook","最新版 PRD FR-1.10"),
      pi("工作记忆保存当前会话/节点与运行子图上下文；短期记忆保存项目内跨会话决策、产物索引、驳回原因和近期复盘","最新版 PRD FR-1.11"),
      pi("长期记忆跨任务沉淀历史摘要、复盘蒸馏、个人训练、组织知识及代码库 L0–L4 索引","最新版 PRD FR-1.11 / FR-1.16"),
      pi("上下文装配、压缩、证据外置和分层检索统一由 Harness 管理；Worker 不保有独立长期记忆","最新版 PRD FR-2.2"),
    ];
  }

  const model=get("model-management");
  if(model) model.children.push(
    pi("Worker 以 skills / rules / hooks / MCP工具声明四件套注册并版本化；平台按节点把 bounded work 编译为具体 Worker 可执行形式","最新版 PRD FR-1.22 Compile-to-Worker")
  );

  const graph=get("graph-definition");
  if(graph){
    graph.title="① Harness 执行图、版本与运行治理（DAG 基座已落地）";
    graph.children.unshift(pi("执行图是 Harness 工程的当前规划形态：v0.1 采用模板图，未来由 Harness 动态建图与调图","最新版 PRD FR-2.1"));
    graph.children.push(pi("项目创建采用交互式渐进规划：导入背景 → 建议模板 → 逐节点确认 owner/Agent/检查包/模型/开关/预算 → 固化图版本","最新版 PRD FR-2.2"));
  }
  const runtime=get("node-runtime");
  if(runtime) runtime.children.push(
    pi("Harness 把目标、输入、产物、约束和裁剪后的工具子集编译为 bounded work；Worker 在全新上下文执行并返回结构化证据","最新版 PRD FR-2.2"),
    pi("pre-hook 注入上下文/规则/检查包，post-hook 强制门禁；失败策略、覆盖关系与触发结果全部留痕","最新版 PRD FR-2.4")
  );
  const graphAccept=get("graph-accept");
  if(graphAccept) graphAccept.title="⑥ 通用执行图验收（依赖与状态测试已具备）";

  const metrics=get("product-metrics");
  if(metrics) metrics.children=[
    pi("三项 100% 护栏：过程透明、审查条目化、最终产品交付发布人类审核（不可下放）","最新版 PRD 结果指标"),
    pi("R1 交付周期压缩率 ≥25%；R2 缺陷逃逸率 ≤5%；R3 AI 产物采纳率 ≥60%，低于 40% 触发 Kill Criteria","最新版 PRD FR-5.4"),
    pi("C1 单任务成本比 ≤0.5，>1.0 连续两周触发复盘；C2 单次确认 ≤15 分钟且 owner 日队列 ≤8","最新版 PRD FR-5.4"),
    pi("指标按租户/项目/节点/Work Item/会话/Agent 聚合，口径版本化并可回到原始证据","最新版 PRD FR-5.4"),
  ];

  const workbench=get("personal-workbench");
  if(workbench) workbench.children.push(
    pi("工作台、项目页和节点页提供常驻快捷动作：规划、审查报告、复审/互审、回归测试、发布门禁报告、登记 Issue；权限与审计路径不变","最新版 PRD FR-8.5")
  );

  const content=get("content-creation");
  replaceChild(content,"左标题目录","内容创作采用左右分栏：左侧标题目录，右侧生成/编辑区，右下悬浮对话可收起","最新版 PRD FR-1.15");

  const billing=get("billing-roadmap");
  if(billing){
    billing.children[2].title="里程碑：M0 技术选型与 Harness/Worker 方案 → M1 V0 规范 → M2 团队 dogfood → M3 对外产品化";
    billing.children[2].src="最新版 PRD 里程碑";
    billing.children[3].title="开放决策新增：auto_confirm 责任法务口径、Worker 注册/目录/能力声明；检查条目归统一企业/领域知识库维护已确认";
    billing.children[3].src="最新版 PRD 开放问题";
  }

  const planning= TREE.children.find(node=>node.id==="domain-planning");
  if(planning){
    planning.title="③ Harness 工程、执行图与任务编排";
    planning.summary="Harness 控制面、模板装配、执行图、运行子图、bounded work 与 SDLC 计划";
    planning.desc="回答‘项目怎样被建模、拆解、约束和调度’。执行图是当前规划形态，Harness 负责渐进式规划、微观拆解、上下文装配、工具裁剪与状态驱动；Worker 只执行边界明确的任务。";
    add(planning,{id:"harness-control",type:"product",impl:"plan",title:"Harness 控制面与黑盒 Worker 边界",children:[
      pi("Harness 自研任务生成、编排、调度、状态、证据、观测、异常决策与成本权限收口","最新版 PRD 平台定位 / FR-2.2"),
      pi("Coding Agent 仅作可替换黑盒 Worker；不自主决定任务边界，不携带上游历史，不直接持有凭据","最新版 PRD FR-2.2"),
      pi("三阶段专业 Agent 是逻辑能力角色，Harness 将其路由到 Codex/Claude Code/内部 Agent 等执行体","最新版 PRD FR-2.2"),
      pi("单 Agent 常驻工具建议 ≤8、MCP 工具 <10；超出任务工具子集的调用由 Harness 拒绝并留痕","最新版 PRD bounded work")
    ]});
  }

  const execution=TREE.children.find(node=>node.id==="domain-execution");
  if(execution) add(execution,{id:"agent-config-scan",type:"product",impl:"plan",title:"Agent 定义、Hook、MCP 与规则包配置自扫",children:[
    pi("注册、版本变更与周期复扫时检查硬编码密钥、令牌和连接串，命中即阻断发布","最新版 PRD Agent 定义与配置自扫"),
    pi("对工具白名单、MCP Server、Hook 执行体形成权限风险画像；高危配置须安全管理员审批","最新版 PRD 安全边界"),
    pi("扫描 system prompt、规则包、Hook 脚本和外部知识携带的可疑注入片段，实施边界隔离","最新版 PRD 安全边界"),
    pi("扫描结果、处置、复扫和豁免全量留痕并可导出，不允许失败后静默启用","最新版 PRD FR-1.21")
  ]});

  const quality=TREE.children.find(node=>node.id==="domain-quality");
  if(quality) add(quality,{id:"release-human-gate",type:"product",impl:"plan",title:"不可下放的产品交付发布人类门",children:[
    pi("节点内三阶段自动化与节点出口 Agent 互审可以配置，但不改变最终交付发布责任","最新版 PRD 人工介入两层模型"),
    pi("研发域发布固定经过技术评审 + CAB，人类审核覆盖率必须 100%，任何开关不可关闭","最新版 PRD 结果指标 / FR-4.5"),
    pi("发布门输入包含审查报告、回归证据、风险、变更范围、回退方案与未决 Issue","最新版 PRD 发布门禁"),
    pi("最终签字身份、时间、材料版本和决策依据写入不可篡改审计链，Agent 不得代签","最新版 PRD 安全边界")
  ]});

  const collaboration=TREE.children.find(node=>node.id==="domain-collaboration");
  if(collaboration) add(collaboration,{id:"operator-actions",type:"product",impl:"plan",title:"受控快捷动作条",children:[
    pi("在工作台、项目页和节点页随上下文显示可用动作；角色或状态不满足时禁用并说明原因","最新版 PRD FR-8.5"),
    pi("一键动作仍走原权限、门禁、日志和成本路径，不提供绕过确认或发布门的捷径","最新版 PRD FR-6.3 / FR-6.4")
  ]});

  const platform=TREE.children.find(node=>node.id==="domain-platform-ops");
  if(platform){
    add(platform,{id:"platform-positioning",type:"product",impl:"plan",title:"企业级 Harness 控制面定位",children:[
      pi("ECC 等是个人/小组增强 coding agent 的配置层；本平台把 coding agent 作为 Worker 统一纳管","最新版 PRD 产品目标"),
      pi("平台边界是多租户、RBAC、审计、成本、证据、异常决策与交付发布门禁，不与 coding agent 本身竞争","最新版 PRD 产品目标"),
      pi("Agent 员工（专家 Agent）与人类员工位于同一身份平面，均受组织、岗位、角色和资源范围约束","最新版 PRD 背景 / FR-1.12")
    ]});
    add(platform,{id:"m0-architecture",type:"product",impl:"plan",title:"M0 技术选型：稳定内核与可退化层",children:[
      pi("稳定内核：图调度/状态机、Work Item、证据哈希、产物版本、RBAC、凭据、审计与成本计量，强调强一致和安全","最新版 PRD M0 技术选型原则"),
      pi("可退化层：上下文装配压缩、微观规划、检索增强、建议生成与看板聚合，允许重试、降级和快速迭代","最新版 PRD M0 技术选型原则"),
      pi("M0 两周内完成 Go/Rust 取舍、Harness 架构、Compile-to-Worker、模型凭据和部署形态决策","最新版 PRD 里程碑")
    ]});
  }
})();
"""


def replace_article(source: str, panel: str) -> str:
    pattern = re.compile(r'<aside id="articlePanel".*?</aside>\s*<script>', re.S)
    replaced, count = pattern.subn(panel + "\n<script>", source, count=1)
    if count != 1:
        raise RuntimeError("Could not replace the v15 article panel")
    return replaced


def build() -> None:
    document = Document(SOURCE_DOCX)
    article, toc, stats = build_article(document)
    source = SOURCE_HTML.read_text(encoding="utf-8")

    source = source.replace(
        "firstFlight 人机混动软件开发系统 · 工作进展全景 v15（PRD 原文联动版）",
        "firstFlight 人机混动软件开发系统 · 工作进展全景 v16（最新 PRD 联动版）",
    )
    source = source.replace("进展全景 v15 · PRD 原文联动版", "进展全景 v16 · 最新 PRD 联动版")
    source = source.replace('title:"firstFlight 工作进展全景 · v15"', 'title:"firstFlight 工作进展全景 · v16"')
    source = source.replace("firstflight_mindmap_node_edits_v15", "firstflight_mindmap_node_edits_v16")
    source = source.replace("业务域大纲 v15", "业务域大纲 v16")
    source = source.replace(
        "v15 改动：",
        "v16 改动：\n     ① 全部 PRD 原文切换为最新 (10) 版，并按最新业务口径更新节点；\n     ② 每个节点卡片增加直接“原文”按钮，侧栏保持打开时可连续切换节点定位；\n     ③ 核心范式更新为 Harness 工程，并补入不可关闭发布门、独立评审、防篡改、三层记忆、规则包、Worker 四件套与配置自扫。\n   v15 改动：",
        1,
    )
    source = source.replace("</style>", V16_CSS + "\n</style>", 1)
    source = replace_article(source, article_panel(article, toc).replace(
        "连续文章视图 · 无 Word 分页",
        "最新版 PRD (10) · 连续文章视图 · 无 Word 分页",
    ))

    js_start = source.index("/* ---- v15 PRD 连续文章侧栏与节点定位 ---- */")
    js_end = source.index("let activeNodeEditId=null;", js_start)
    source = source[:js_start] + ARTICLE_JS + "\n" + source[js_end:]

    marker = "/* v14 汇报口径的证据索引：状态升级必须能回指当前工程或已接入平台。 */"
    source = source.replace(marker, V16_CONTENT_JS + "\n\n" + marker, 1)

    old_card = '  else inner+=`<div class="ttl">${escapeHtml(n.title)}</div>`;\n  inner+=`</div>`;'
    new_card = '  else inner+=`<div class="ttl">${escapeHtml(n.title)}</div>`;\n  inner+=`<button class="node-doc-jump" type="button" title="直接定位最新版 PRD 原文" aria-label="查看此节点对应的最新版 PRD 原文">原文</button>`;\n  inner+=`</div>`;'
    if old_card not in source:
        raise RuntimeError("Could not add the direct PRD button to nodes")
    source = source.replace(old_card, new_card, 1)

    old_events = '  const tg=el.querySelector(".toggle");\n  if(tg) tg.addEventListener("click",e=>{ e.stopPropagation(); toggle(n); });'
    new_events = '  const docJump=el.querySelector(".node-doc-jump");\n  if(docJump) docJump.addEventListener("click",e=>{ e.stopPropagation(); state.selected=n.id; document.querySelectorAll("#nodes .node.sel").forEach(node=>node.classList.remove("sel")); el.classList.add("sel"); openPrdArticle(n); });\n  const tg=el.querySelector(".toggle");\n  if(tg) tg.addEventListener("click",e=>{ e.stopPropagation(); toggle(n); });'
    if old_events not in source:
        raise RuntimeError("Could not bind the direct PRD button")
    source = source.replace(old_events, new_events, 1)

    source = source.replace(
        "任意节点可编辑 · 查看 PRD 原文可同页定位 · 点节点右侧 + 逐层展开",
        "任意节点可编辑 · 点节点内“原文”可连续切换最新版 PRD 定位 · 点节点右侧 + 逐层展开",
    )

    OUTPUT_HTML.write_text(source, encoding="utf-8")
    print(f"wrote={OUTPUT_HTML}")
    print("stats=" + repr(stats))
    print(f"bytes={OUTPUT_HTML.stat().st_size}")


if __name__ == "__main__":
    build()
