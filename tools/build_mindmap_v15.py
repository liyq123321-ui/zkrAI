from __future__ import annotations

import base64
import html
import re
from pathlib import Path

from docx import Document
from docx.document import Document as _Document
from docx.oxml.ns import qn
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph


ROOT = Path(r"C:\Users\zkr\Documents\ChatGPT\firstFlight Project")
SOURCE_HTML = ROOT / "output" / "firstflight_mindmap_v14.html"
OUTPUT_HTML = ROOT / "output" / "firstflight_mindmap_v15.html"
SOURCE_DOCX = Path(
    r"C:\Users\zkr\Documents\WXWork\1688857625913676\Cache\File\2026-09\人机混动软件工程平台 PRD（v0.1） (7).docx"
)


ARTICLE_CSS = r"""
  /* v15 连续 PRD 原文侧栏：Markdown 式阅读，不使用 Word 分页。 */
  #articlePanel{position:fixed;top:54px;right:0;width:min(720px,72vw);height:calc(100% - 54px);z-index:45;
    background:#0b0f14f7;border-left:1px solid #30363d;box-shadow:-16px 0 36px rgba(0,0,0,.42);
    transform:translateX(102%);transition:transform .22s ease;display:flex;flex-direction:column;backdrop-filter:blur(8px);}
  #articlePanel.open{transform:translateX(0);}
  .article-head{display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid #30363d;background:#11161d;}
  .article-head-main{min-width:0;flex:1;}
  .article-head strong{display:block;font-size:13.5px;color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .article-head small{display:block;color:#8b949e;font-size:10.5px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .article-close{font-size:18px;line-height:1;padding:5px 8px;}
  .article-search{display:flex;gap:7px;padding:10px 14px;border-bottom:1px solid #21262d;background:#0d1117;}
  .article-search input{flex:1;min-width:0;background:#070b10;border:1px solid #30363d;color:#e6edf3;border-radius:7px;padding:7px 9px;font-size:12px;outline:none;}
  .article-search input:focus{border-color:#58a6ff;box-shadow:0 0 0 2px #58a6ff22;}
  .article-layout{display:grid;grid-template-columns:185px minmax(0,1fr);min-height:0;flex:1;}
  .article-toc{overflow:auto;border-right:1px solid #21262d;padding:10px 8px;background:#0d1117;}
  .article-toc button{display:block;width:100%;border:0;background:transparent;color:#8b949e;text-align:left;padding:5px 7px;border-radius:6px;font-size:10.5px;line-height:1.35;cursor:pointer;}
  .article-toc button:hover,.article-toc button.active{background:#1f6feb22;color:#a5d6ff;}
  .article-toc .lv2{padding-left:15px;}
  .article-toc .lv3{padding-left:23px;font-size:10px;}
  .article-scroll{overflow:auto;scroll-behavior:smooth;}
  .prd-article{max-width:860px;margin:0 auto;padding:30px 42px 80px;color:#c9d1d9;font-size:13px;line-height:1.78;}
  .prd-article .doc-title{font-size:28px;line-height:1.25;color:#f0f6fc;margin:0 0 30px;font-weight:750;letter-spacing:-.4px;}
  .prd-article h2,.prd-article h3,.prd-article h4,.prd-article h5{scroll-margin-top:22px;color:#f0f6fc;line-height:1.4;font-weight:700;}
  .prd-article h2{font-size:22px;margin:42px 0 14px;padding-bottom:8px;border-bottom:1px solid #30363d;}
  .prd-article h3{font-size:18px;margin:32px 0 11px;}
  .prd-article h4{font-size:15px;margin:25px 0 9px;color:#dbeafe;}
  .prd-article h5{font-size:13.5px;margin:21px 0 8px;color:#a5d6ff;}
  .prd-article p{margin:8px 0;}
  .prd-article p.strong-line{font-weight:650;color:#e6edf3;margin-top:13px;}
  .prd-article p.list-line{position:relative;padding-left:17px;}
  .prd-article p.list-line::before{content:"•";position:absolute;left:2px;color:#58a6ff;}
  .prd-article strong{color:#f0f6fc;font-weight:650;}
  .prd-article em{color:#d2a8ff;}
  .prd-table-wrap{overflow:auto;margin:15px 0 22px;border:1px solid #30363d;border-radius:8px;}
  .prd-article table{border-collapse:collapse;width:100%;min-width:520px;font-size:11.5px;line-height:1.5;background:#0d1117;}
  .prd-article th,.prd-article td{border:1px solid #30363d;padding:7px 9px;vertical-align:top;text-align:left;}
  .prd-article tr:first-child th,.prd-article tr:first-child td{background:#172033;color:#e6edf3;font-weight:650;}
  .prd-article tr:nth-child(even) td{background:#11161d;}
  .prd-article figure{margin:20px 0 26px;text-align:center;}
  .prd-article figure img,.prd-article p img{display:block;max-width:100%;height:auto;margin:12px auto;border-radius:8px;background:#fff;box-shadow:0 5px 22px rgba(0,0,0,.32);}
  .prd-article figcaption{color:#8b949e;font-size:11px;margin-top:7px;}
  .article-target{animation:articlePulse 1.7s ease;}
  .article-search-hit{outline:2px solid #eab308;outline-offset:5px;border-radius:4px;}
  @keyframes articlePulse{0%,100%{background:transparent}25%,70%{background:#1f6feb2c}}
  @media (max-width:900px){#articlePanel{width:100%;max-width:none}.article-layout{grid-template-columns:1fr}.article-toc{display:none}.prd-article{padding:24px 22px 70px}}
"""


ARTICLE_JS = r"""
/* ---- v15 PRD 连续文章侧栏与节点定位 ---- */
const NODE_PRD_SECTION = {
  root:"人机混动软件工程平台 PRD",
  "domain-organization":"FR-1.5 空间管理",
  "domain-requirements":"背景与用户问题",
  "domain-planning":"图工程编排",
  "domain-execution":"研发主链路：代码工作流集成方案",
  "domain-quality":"审查与确认机制",
  "domain-collaboration":"FR-8.5 个人工作台",
  "domain-platform-ops":"产品总框架",
  "tenant-identity":"FR-1.6 身份接入与角色体系",
  "org-role":"FR-1.12 组织架构管理",
  "rbac-scope":"FR-1.6 身份接入与角色体系",
  "avatar-team":"FR-1.1 员工与 Agent 分身",
  "space-lifecycle":"FR-1.5 空间管理",
  "identity-accept":"FR-1.6 身份接入与角色体系",
  s0:"背景与用户问题", s1:"背景与用户问题", s2:"内容创作与变更存档", s3:"审查与确认机制",
  "content-creation":"FR-1.13 内容创作",
  "product-domain-model":"领域模型（核心 ER）",
  "template-compile":"FR-1.9 模板管理",
  "graph-definition":"FR-2.1 图定义",
  "node-runtime":"FR-2.4 节点三阶段",
  s4:"FR-2.4 节点三阶段",
  "sdlc-hub":"优先级与里程碑",
  "automation-switches":"FR-4.4",
  "capability-tool":"FR-1.8 能力与工具",
  "model-management":"FR-1.22 模型管理",
  "skill-hub":"FR-1.4 Skill",
  s5:"研发主链路：代码工作流集成方案",
  "sandbox-codeflow":"研发主链路：代码工作流集成方案",
  "code-hub":"一次代码工作流闭环",
  "capability-accept":"FR-1.21 能力与工具引入",
  "review-confirmation":"FR-4.1 Agent 自查",
  "dual-hub":"FR-4.3 复审机制",
  "issue-management":"FR-3.5",
  "data-hub":"系统架构与领域模型",
  "graph-accept":"FR-2.5 图观测",
  "accept-hub":"审查与确认机制",
  s6:"一次代码工作流闭环",
  "kill-criteria":"Kill Criteria",
  "personal-workbench":"FR-8.5 个人工作台",
  "message-todo":"FR-8.2 待办",
  "workitem-kanban":"FR-3.3 看板",
  "product-observation":"FR-5.1",
  "product-metrics":"FR-5.4",
  "collab-accept":"FR-8.5 个人工作台",
  "product-ia":"产品总框架",
  "product-status-contract":"核心概念模型",
  "product-glossary":"核心概念模型",
  "ws-hub":"FR-1.5 空间管理",
  "ws-company":"企业空间",
  "ws-project":"协作空间",
  "ws-personal":"个体空间",
  "ws-knowledge":"FR-1.10 领域知识",
  "ws-skills":"FR-1.4 Skill",
  "knowledge-memory":"FR-1.10 领域知识",
  "plugin-knowledge":"FR-1.18 插件框架",
  "execution-log":"日志细节要求",
  "nfr-security":"安全边界初稿",
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
  articlePanel.classList.add("open");articlePanel.setAttribute("aria-hidden","false");
  const target=n?resolvePrdHeading(n):document.getElementById("prd-title");
  requestAnimationFrame(()=>markArticleTarget(target||document.getElementById("prd-title")));
}
function closePrdArticle(){articlePanel.classList.remove("open");articlePanel.setAttribute("aria-hidden","true");}
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


def iter_block_items(parent: _Document | _Cell):
    parent_element = parent.element.body if isinstance(parent, _Document) else parent._tc
    for child in parent_element.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def run_images(run) -> list[str]:
    images: list[str] = []
    for blip in run._r.xpath(".//a:blip"):
        rel_id = blip.get(qn("r:embed"))
        if not rel_id:
            continue
        part = run.part.related_parts.get(rel_id)
        if part is None:
            continue
        mime = getattr(part, "content_type", "image/png") or "image/png"
        encoded = base64.b64encode(part.blob).decode("ascii")
        images.append(f'<img loading="lazy" src="data:{mime};base64,{encoded}" alt="PRD 内嵌图片">')
    return images


def render_run(run) -> str:
    text = html.escape(run.text or "").replace("\n", "<br>")
    if text:
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.underline:
            text = f"<u>{text}</u>"
    return text + "".join(run_images(run))


def render_inline(paragraph: Paragraph) -> str:
    pieces: list[str] = []
    if hasattr(paragraph, "iter_inner_content"):
        for item in paragraph.iter_inner_content():
            if hasattr(item, "runs") and not hasattr(item, "_r"):
                label = "".join(render_run(run) for run in item.runs)
                url = getattr(item, "url", "")
                pieces.append(f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noreferrer">{label}</a>' if url else label)
            else:
                pieces.append(render_run(item))
    else:
        pieces.extend(render_run(run) for run in paragraph.runs)
    result = "".join(pieces)
    if not re.sub(r"<[^>]+>", "", result).strip() and paragraph.text.strip():
        result = html.escape(paragraph.text.strip())
    return result


def paragraph_size(paragraph: Paragraph) -> float:
    sizes = [run.font.size.pt for run in paragraph.runs if run.font.size]
    return max(sizes, default=11.0)


def heading_level(paragraph: Paragraph) -> int | None:
    if not paragraph.text.strip():
        return None
    size = paragraph_size(paragraph)
    if size >= 25:
        return 0
    if size >= 18:
        return 1
    if size >= 16:
        return 2
    if size >= 15:
        return 3
    if size >= 14:
        return 4
    return None


def paragraph_has_numbering(paragraph: Paragraph) -> bool:
    properties = paragraph._p.pPr
    return bool(properties is not None and properties.numPr is not None)


def paragraph_is_bold(paragraph: Paragraph) -> bool:
    visible = [run for run in paragraph.runs if run.text.strip()]
    return bool(visible) and all(bool(run.bold) for run in visible)


def render_table(table: Table) -> str:
    rows: list[str] = []
    for row_index, row in enumerate(table.rows):
        cells: list[str] = []
        tag = "th" if row_index == 0 else "td"
        for cell in row.cells:
            contents: list[str] = []
            for paragraph in cell.paragraphs:
                value = render_inline(paragraph).strip()
                if value:
                    contents.append(value)
            cells.append(f"<{tag}>{'<br>'.join(contents)}</{tag}>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f'<div class="prd-table-wrap"><table>{"".join(rows)}</table></div>'


def build_article(document: _Document) -> tuple[str, str, dict[str, int]]:
    paragraph_index = {id(p._p): index for index, p in enumerate(document.paragraphs)}
    headings: list[tuple[int, str, str]] = []
    body: list[str] = []
    stats = {"paragraphs": 0, "tables": 0, "images": 0, "headings": 0}
    pending_caption: str | None = None

    for block in iter_block_items(document):
        if isinstance(block, Table):
            body.append(render_table(block))
            stats["tables"] += 1
            continue

        stats["paragraphs"] += 1
        text = " ".join(block.text.split())
        inline = render_inline(block).strip()
        image_count = len(block._p.xpath(".//a:blip"))
        stats["images"] += image_count
        if not text and not image_count:
            continue

        level = heading_level(block)
        index = paragraph_index.get(id(block._p), stats["paragraphs"])
        if level is not None:
            anchor = "prd-title" if level == 0 else f"prd-section-{index}"
            fr_codes = ",".join(dict.fromkeys(re.findall(r"FR-\d+(?:\.\d+)?", text, flags=re.I)))
            tag = "h1" if level == 0 else f"h{min(5, level + 1)}"
            class_name = ' class="doc-title"' if level == 0 else ""
            body.append(
                f'<{tag}{class_name} id="{anchor}" data-prd-heading data-heading-text="{html.escape(text, quote=True)}" data-fr="{html.escape(fr_codes, quote=True)}">{inline or html.escape(text)}</{tag}>'
            )
            if level > 0:
                headings.append((level, anchor, text))
            stats["headings"] += 1
            pending_caption = None
            continue

        if image_count and not text:
            caption = html.escape(pending_caption or "PRD 图示")
            body.append(f'<figure>{inline}<figcaption>{caption}</figcaption></figure>')
            pending_caption = None
            continue

        classes: list[str] = []
        if paragraph_has_numbering(block):
            classes.append("list-line")
        if paragraph_is_bold(block) or text in {"验收：", "需求：", "说明："}:
            classes.append("strong-line")
        class_attr = f' class="{" ".join(classes)}"' if classes else ""
        body.append(f"<p{class_attr}>{inline or html.escape(text)}</p>")
        pending_caption = text if re.match(r"^图\s*\d+", text) else None

    toc = "".join(
        f'<button type="button" class="lv{min(level,3)}" data-article-target="{anchor}">{html.escape(text)}</button>'
        for level, anchor, text in headings
        if level <= 3
    )
    return "\n".join(body), toc, stats


def article_panel(article: str, toc: str) -> str:
    return f"""
<aside id="articlePanel" aria-hidden="true" aria-label="PRD 原文侧栏">
  <header class="article-head">
    <button class="btn" id="articleBack" type="button">返回节点</button>
    <div class="article-head-main">
      <strong>人机混动软件工程平台 PRD v0.1</strong>
      <small id="articleLocation">连续文章视图 · 无 Word 分页</small>
    </div>
    <button class="btn article-close" id="articleClose" type="button" aria-label="关闭 PRD 原文">×</button>
  </header>
  <div class="article-search">
    <input id="articleSearchInput" type="search" placeholder="在 PRD 原文中搜索…" />
    <button class="btn" id="articleSearchBtn" type="button">查找</button>
  </div>
  <div class="article-layout">
    <nav class="article-toc" aria-label="PRD 章节目录">{toc}</nav>
    <div class="article-scroll" id="articleScroll">
      <article class="prd-article" id="prdArticle">{article}</article>
    </div>
  </div>
</aside>
"""


def build() -> None:
    document = Document(SOURCE_DOCX)
    article, toc, stats = build_article(document)
    source = SOURCE_HTML.read_text(encoding="utf-8")

    source = source.replace(
        "firstFlight 人机混动软件开发系统 · 工作进展全景 v14（领导展示版）",
        "firstFlight 人机混动软件开发系统 · 工作进展全景 v15（PRD 原文联动版）",
    )
    source = source.replace("进展全景 v14 · 领导展示版", "进展全景 v15 · PRD 原文联动版")
    source = source.replace("title:\"firstFlight 工作进展全景 · v14\"", "title:\"firstFlight 工作进展全景 · v15\"")
    source = source.replace("firstflight_mindmap_node_edits_v14", "firstflight_mindmap_node_edits_v15")
    source = source.replace("业务域大纲 v13", "业务域大纲 v15")
    source = source.replace(
        "   v14 改动：",
        "   v15 改动：\n     ① 新增同页 PRD 连续文章侧栏，保留标题、正文、表格与图片，取消 Word 分页；\n     ② 节点详情新增“查看 PRD 原文”，优先按 FR 编号定位，再按业务映射与标题相似度定位；\n     ③ 顶栏可独立打开 PRD 原文，并支持章节目录与全文查找。\n   v14 改动：",
    )
    source = source.replace("</style>", ARTICLE_CSS + "\n</style>", 1)
    source = source.replace(
        '  <button class="btn" id="btnMd">导出大纲</button>',
        '  <button class="btn" id="btnMd">导出大纲</button>\n  <button class="btn" id="btnArticle" type="button">打开 PRD 原文</button>',
        1,
    )
    source = source.replace(
        '    <button class="btn edit-primary" id="pEditOpen" type="button">编辑</button>',
        '    <button class="btn edit-primary" id="pEditOpen" type="button">编辑</button>\n    <button class="btn" id="pArticleOpen" type="button">查看 PRD 原文</button>',
        1,
    )
    source = source.replace("<script>", article_panel(article, toc) + "\n<script>", 1)
    source = source.replace("let activeNodeEditId=null;", ARTICLE_JS + "\nlet activeNodeEditId=null;", 1)
    source = source.replace(
        "任意节点可编辑 · 点节点右侧 + 逐层展开",
        "任意节点可编辑 · 查看 PRD 原文可同页定位 · 点节点右侧 + 逐层展开",
    )
    OUTPUT_HTML.write_text(source, encoding="utf-8")
    print(f"wrote={OUTPUT_HTML}")
    print("stats=" + repr(stats))
    print(f"bytes={OUTPUT_HTML.stat().st_size}")


if __name__ == "__main__":
    build()
