"""Generate one reviewable PRD preview from the personal WeKnora template KB."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

import httpx

from app.config import Settings
from app.database.database import SessionLocal
from app.database.models import Project, SpecVersion, WorkItem
from app.services.weknora import WeKnoraClient


TEMPLATE_KB_ID = "d029257d-d090-4b26-b6c9-3be0af0a7ac5"
KB_CITATION = re.compile(r"<kb\b[^>]*/>", re.IGNORECASE)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _project_input(project_id: str) -> tuple[Project, SpecVersion, WorkItem]:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project is None or not project.current_spec_version_id:
            raise RuntimeError("project or current PRD was not found")
        spec = db.get(SpecVersion, project.current_spec_version_id)
        root = db.query(WorkItem).filter_by(project_id=project.id, kind="ROOT").one()
        if spec is None:
            raise RuntimeError("current PRD was not found")
        db.expunge(project)
        db.expunge(spec)
        db.expunge(root)
        return project, spec, root


def _prompt(root: WorkItem, spec: SpecVersion) -> str:
    return f"""你是产品经理。请严格使用已指定“PRD模板”知识库中的模板结构，改写下面的现有 PRD。

硬性要求：
1. 只调整文档结构、标题、表格和表达方式；必须完整保留现有 PRD 的业务事实、范围、约束、编号、数值、异常规则和验收口径。
2. 不得新增现有 PRD 没有说明的功能、角色、流程、指标、排期、接口、技术方案或业务结论。未知项写“待确认”，不要猜测。
3. 使用完整、清晰的中文 Markdown；第一行必须是“# {root.title} 产品需求文档”。
4. 保留所有 FR-*、NFR-* 编号，并把功能需求、非功能需求、验收标准尽量整理成模板化表格。
5. 输出必须是完整 PRD 正文，不要解释改写过程，不要输出引用标记，不要输出 diff，不要输出 HTML，不要输出原型。
6. 用标记 BEGIN_PRD 和 END_PRD 包住正文，标记之外不要输出任何文字。

现有 PRD（唯一业务事实来源）：

{spec.markdown}
"""


def _extract_answer(raw: str) -> str:
    answer: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:])
        except json.JSONDecodeError:
            continue
        if event.get("response_type") == "answer":
            answer.append(str(event.get("content") or ""))
    joined = KB_CITATION.sub("", "".join(answer)).strip()
    match = re.search(r"BEGIN_PRD\s*(.*?)\s*END_PRD", joined, re.DOTALL)
    return (match.group(1) if match else joined).strip() + "\n"


def _validate(markdown: str, source: str) -> dict[str, object]:
    source_ids = sorted(set(re.findall(r"\b(?:FR|NFR)-\d{3}\b", source)))
    missing_ids = [item for item in source_ids if item not in markdown]
    required_terms = ["背景", "目标", "用户", "功能需求", "非功能需求", "验收"]
    missing_terms = [item for item in required_terms if item not in markdown]
    forbidden = [
        marker for marker in ("<html", "<!doctype", "BEGIN_PRD", "END_PRD", "<kb ")
        if marker.casefold() in markdown.casefold()
    ]
    checks = {
        "minimum_length": len(markdown) >= 2500,
        "markdown_title": markdown.startswith("# "),
        "section_count": len(re.findall(r"^##+ ", markdown, re.MULTILINE)) >= 8,
        "all_requirement_ids_preserved": not missing_ids,
        "template_sections_present": not missing_terms,
        "no_html_diff_or_kb_markers": not forbidden,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "characters": len(markdown),
        "sections": len(re.findall(r"^##+ ", markdown, re.MULTILINE)),
        "source_requirement_ids": source_ids,
        "missing_requirement_ids": missing_ids,
        "missing_template_terms": missing_terms,
        "forbidden_markers": forbidden,
    }


async def _generate(project_id: str, output: Path) -> None:
    project, spec, root = _project_input(project_id)
    settings = Settings.from_env()
    weknora = WeKnoraClient(settings)
    session = await weknora.personal_session()
    created = await weknora._request(
        "POST",
        "/sessions",
        token=session.token,
        json={
            "title": f"firstFlight PRD 模板改写 - {root.title}",
            "description": f"项目 {project.id} 的只读候选 PRD",
        },
        expected=(200, 201),
    )
    session_data = created.get("data") or created
    session_id = str(session_data.get("id") or "")
    if not session_id:
        raise RuntimeError("WeKnora did not return a session id")

    started = time.perf_counter()
    async with httpx.AsyncClient(base_url=settings.weknora_base_url, timeout=300) as client:
        response = await client.post(
            f"/knowledge-chat/{session_id}",
            headers={"Authorization": f"Bearer {session.token}", "Accept": "text/event-stream"},
            json={
                "query": _prompt(root, spec),
                "knowledge_base_ids": [TEMPLATE_KB_ID],
                "web_search_enabled": False,
                "disable_title": True,
            },
        )
        response.raise_for_status()
    elapsed = round(time.perf_counter() - started, 3)
    markdown = _extract_answer(response.text)
    report = _validate(markdown, spec.markdown)
    report.update({
        "project_id": project.id,
        "project_title": root.title,
        "source_spec_id": spec.id,
        "source_revision": spec.revision,
        "candidate_revision": spec.revision + 1,
        "weknora_session_id": session_id,
        "generation_seconds": elapsed,
        "template_knowledge_base_id": TEMPLATE_KB_ID,
    })

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    report_path = output.with_suffix(".validation.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "validation": str(report_path), **report}, ensure_ascii=False))


if __name__ == "__main__":
    args = _arguments()
    asyncio.run(_generate(args.project_id, args.output))
