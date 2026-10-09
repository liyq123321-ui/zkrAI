"""Create editable, version-bound presentations from a frozen PRD."""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import os
from html import escape
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.codex import BUNDLED_SKILL_ROOT, CodexStructuredRunner, _bundled_skill_digest, build_node_prompt
from app.agents.output_validation import OutputConsistencyError
from app.config import Settings
from app.database.models import AuditEvent, PrdPresentation, Project, SpecVersion


Audience = Literal["team", "external"]


class SlidePlan(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    kicker: str = Field(max_length=35)
    takeaway: str = Field(min_length=1, max_length=130)
    bullets: list[str] = Field(min_length=1, max_length=4)
    source_quote: str = Field(max_length=180)
    speaker_notes: str = Field(max_length=500)


class PresentationPlan(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(max_length=140)
    slides: list[SlidePlan] = Field(min_length=5, max_length=9)


class DraftPresentationResult(BaseModel):
    """A rendered PPTX built from an unfrozen PRD draft (not version-bound)."""

    title: str
    pptx: bytes
    plan: dict
    preview_html: str


def _source_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _presentation_objective(audience: Audience, stage: str) -> str:
    """Build the planning objective. ``stage='draft'`` relaxes the frozen-PRD assumption."""

    source = (
        "from a frozen PRD"
        if stage == "frozen"
        else "from the current PRD draft (the PRD is NOT final yet; treat it as a work-in-progress draft)"
    )
    return (
        f"You are a product manager preparing an editable presentation {source}. "
        "Use the installed ppt-generation Skill for story arc and slide planning only. "
        "Do not call image generation, run scripts, or write files; the host application renders "
        "your structured plan with officecli-skill. Use a consistent restrained visual rhythm. "
        "Return 5-9 slides; every slide has one takeaway and at most 4 short bullets. "
        "Copy source_quote verbatim from the supplied PRD for every substantive claim. "
        "Never invent metrics, customers, launch dates, pricing, or approvals. "
        + ("Team audience: explain problem, users, scope, key flows, decisions, acceptance, and unresolved risks. "
           "State unknowns as unknowns. Include practical speaker notes. " if audience == "team" else
           "External audience: explain the customer problem, value, use cases, and supported capabilities. "
           "Exclude internal implementation, unresolved risks, private operational detail, and unapproved roadmap. "
           "Do not claim market leadership or quantified outcomes without PRD evidence. ")
    )


@functools.lru_cache(maxsize=1)
def _checked_officecli() -> Path:
    """Verify the bundled officecli-skill once per process and reuse the result."""

    root = BUNDLED_SKILL_ROOT / "officecli-skill"
    manifest = json.loads((BUNDLED_SKILL_ROOT / "officecli-skill-source.json").read_text(encoding="utf-8"))
    if manifest.get("name") != "officecli-skill" or _bundled_skill_digest(root) != manifest.get("tree_sha256"):
        raise RuntimeError("officecli-skill integrity check failed")
    binary = root / ("officecli.exe" if os.name == "nt" else "officecli")
    if not binary.is_file():
        raise RuntimeError("officecli is not available on this host")
    return binary


PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _slide_notes(slide: SlidePlan, audience: Audience) -> str:
    """Speaker notes, plus the page's verbatim PRD excerpt on the team deck."""

    if audience != "team" or not slide.source_quote:
        return slide.speaker_notes
    quote = f"【PRD 原文】\n{slide.source_quote}"
    return f"{slide.speaker_notes}\n\n{quote}" if slide.speaker_notes else quote


def _render_editable_pptx(plan: PresentationPlan, audience: Audience, stage: str = "frozen") -> bytes:
    binary = _checked_officecli()
    footer = (
        "基于已冻结 PRD · 可编辑演示文稿"
        if stage == "frozen"
        else "基于 PRD 草稿 · 可编辑演示文稿（未定稿）"
    )
    dark = audience == "team"
    background = "#101B2D" if dark else "#F7FAF8"
    title_color = "#FFFFFF" if dark else "#102D29"
    body_color = "#D6E6E1" if dark else "#34544D"
    accent = "#73D5AE" if dark else "#16835E"
    card = "#1B2A3D" if dark else "#EAF3EE"
    env = os.environ.copy()
    env["OFFICECLI_NO_AUTO_RESIDENT"] = "1"

    with tempfile.TemporaryDirectory(prefix="firstflight-prd-ppt-") as directory:
        output = Path(directory) / "presentation.pptx"

        def office(*args: str, stdin: str | None = None) -> None:
            result = subprocess.run(
                [str(binary), *args], cwd=directory, env=env, input=stdin,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"officecli {args[0]} failed: {(result.stderr or result.stdout)[-400:]}")

        def shape(slide: int, text: str, x: float, y: float, width: float, height: float,
                  size: int, color: str, *, fill: str = "none", bold: bool = False) -> dict:
            return {
                "command": "add", "parent": f"/slide[{slide}]", "type": "shape",
                "props": {
                    "text": text, "x": f"{x}in", "y": f"{y}in", "width": f"{width}in",
                    "height": f"{height}in", "size": f"{size}pt", "color": color,
                    "fill": fill, "bold": str(bold).lower(),
                    "font": "Microsoft YaHei", "autoFit": "shrink",
                },
            }

        office("create", str(output))
        operations: list[dict] = []
        total = len(plan.slides) + 1
        for index in range(1, total + 1):
            operations.append({
                "command": "add", "parent": "/", "type": "slide",
                "props": {"background": background},
            })
            if index == 1:
                operations.append(shape(index, "PRODUCT STORY  /  " + ("团队沟通" if dark else "产品介绍"),
                                        .72, .65, 11.3, .35, 13, accent, bold=True))
                operations.append(shape(index, plan.title, .72, 1.65, 11.6, 1.45, 42, title_color, bold=True))
                operations.append(shape(index, plan.subtitle, .78, 3.45, 10.8, 1.0, 22, body_color))
                operations.append(shape(index, footer, .78, 6.68, 9.5, .32, 11, body_color))
                continue
            slide = plan.slides[index - 2]
            operations.append(shape(index, f"{index - 1:02d}  /  {slide.kicker}", .72, .48, 11.4, .34, 12, accent, bold=True))
            operations.append(shape(index, slide.title, .72, 1.02, 11.5, .82, 31, title_color, bold=True))
            operations.append(shape(index, slide.takeaway, .75, 1.9, 11.1, .7, 18, body_color))
            for bullet_index, bullet in enumerate(slide.bullets[:4]):
                y = 2.83 + bullet_index * .88
                operations.append(shape(index, "●", .83, y + .1, .26, .3, 14, accent))
                operations.append(shape(index, bullet, 1.19, y, 10.95, .69, 18, title_color,
                                        fill=card if bullet_index == 0 else "none"))
            operations.append(shape(index, f"PRD  ·  {index - 1}/{len(plan.slides)}", .78, 6.72, 10.8, .3, 10, body_color))
            notes = _slide_notes(slide, audience)
            if notes:
                operations.append({"command": "set", "path": f"/slide[{index}]", "props": {"notes": notes}})
        operations.append({"command": "validate"})
        # One batch instead of one subprocess per shape: same edits, a fraction of the process spawns.
        office("batch", str(output), "--json", stdin=json.dumps(operations, ensure_ascii=False))
        return output.read_bytes()


def render_preview_html(plan: PresentationPlan, pptx: bytes, audience: Audience, stage: str = "frozen") -> str:
    """Render the slide plan as a standalone, browsable HTML deck with an embedded download.

    The page is host-templated from already-validated text, so (unlike the LLM-authored
    prototype) it needs no CSP sandbox. The PPTX travels as a base64 data URI because the
    download endpoint requires a bearer token that a new tab cannot carry.
    """

    dark = audience == "team"
    audience_label = "团队沟通" if dark else "产品介绍"
    stage_label = "草稿" if stage != "frozen" else "已冻结"
    background = "#101B2D" if dark else "#F7FAF8"
    surface = "#1B2A3D" if dark else "#FFFFFF"
    title_color = "#FFFFFF" if dark else "#102D29"
    body_color = "#D6E6E1" if dark else "#34544D"
    accent = "#73D5AE" if dark else "#16835E"
    file_name = f"prd-{audience}{'-draft' if stage != 'frozen' else ''}.pptx"
    encoded = base64.b64encode(pptx).decode("ascii")

    def slide_html(index: int, slide: SlidePlan) -> str:
        bullets = "".join(f"<li>{escape(bullet)}</li>" for bullet in slide.bullets[:4])
        notes_body = escape(_slide_notes(slide, audience)).replace("\n", "<br />")
        notes = (
            f"<details class=\"notes\"><summary>演讲备注（含 PRD 原文）</summary><p>{notes_body}</p></details>"
            if notes_body else ""
        )
        return (
            f"<article class=\"slide\">"
            f"<p class=\"kicker\">{index:02d} / {escape(slide.kicker)}</p>"
            f"<h2>{escape(slide.title)}</h2>"
            f"<p class=\"takeaway\">{escape(slide.takeaway)}</p>"
            f"<ul>{bullets}</ul>{notes}</article>"
        )

    slides = "".join(slide_html(index, slide) for index, slide in enumerate(plan.slides, start=1))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{escape(plan.title)} · 在线预览</title>
<style>
:root {{ color-scheme: {"dark" if dark else "light"}; }}
body {{ margin: 0; background: {background}; color: {title_color};
  font-family: "Microsoft YaHei", "PingFang SC", system-ui, sans-serif; }}
header {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center;
  padding: 20px 28px; border-bottom: 1px solid {accent}55; position: sticky; top: 0;
  background: {background}f2; backdrop-filter: blur(6px); }}
header h1 {{ margin: 0; font-size: 20px; flex: 1 1 260px; }}
.tag {{ font-size: 12px; padding: 3px 10px; border-radius: 999px;
  border: 1px solid {accent}; color: {accent}; }}
.download {{ font-size: 14px; font-weight: 700; text-decoration: none; color: {background};
  background: {accent}; padding: 9px 18px; border-radius: 8px; }}
.cover {{ padding: 34px 28px 6px; }}
.cover .kicker {{ color: {accent}; letter-spacing: .12em; font-size: 12px; margin: 0 0 10px; }}
.cover h2 {{ margin: 0 0 10px; font-size: 34px; }}
.cover p {{ margin: 0; color: {body_color}; }}
main {{ display: grid; gap: 18px; padding: 22px 28px 48px; }}
.slide {{ background: {surface}; border: 1px solid {accent}33; border-radius: 14px; padding: 22px 24px; }}
.slide .kicker {{ color: {accent}; font-size: 12px; letter-spacing: .12em; margin: 0 0 8px; }}
.slide h2 {{ margin: 0 0 10px; font-size: 24px; }}
.takeaway {{ margin: 0 0 14px; color: {body_color}; }}
.slide ul {{ margin: 0; padding-left: 20px; color: {body_color}; line-height: 1.7; }}
.notes {{ margin-top: 14px; color: {body_color}; font-size: 13px; }}
.notes summary {{ cursor: pointer; color: {accent}; }}
</style></head>
<body>
<header>
  <h1>{escape(plan.title)}</h1>
  <span class="tag">{audience_label}</span>
  <span class="tag">{stage_label}</span>
  <a class="download" download="{escape(file_name)}" href="data:{PPTX_MIME_TYPE};base64,{encoded}">下载可编辑 PPTX</a>
</header>
<section class="cover">
  <p class="kicker">PRODUCT STORY  /  {audience_label}</p>
  <h2>{escape(plan.title)}</h2>
  <p>{escape(plan.subtitle or "")}</p>
</section>
<main>{slides}</main>
</body></html>"""


class PrdPresentationService:
    def __init__(self, session_factory, settings: Settings):
        self._session_factory = session_factory
        self._settings = settings

    def current(self, session_id: str, audience: Audience) -> PrdPresentation | None:
        with self._session_factory() as db:
            project = db.query(Project).filter_by(session_id=session_id).one_or_none()
            if project is None or not project.current_spec_version_id:
                return None
            return db.query(PrdPresentation).filter_by(
                spec_version_id=project.current_spec_version_id, audience=audience,
            ).one_or_none()

    async def generate(self, session_id: str, audience: Audience, actor_id: str | None = None) -> PrdPresentation:
        with self._session_factory() as db:
            project = db.query(Project).filter_by(session_id=session_id).one_or_none()
            spec = db.get(SpecVersion, project.current_spec_version_id) if project and project.current_spec_version_id else None
            if project is None or spec is None or spec.project_id != project.id:
                raise ValueError("请先冻结 PRD，再生成演示 PPT。")
            existing = db.query(PrdPresentation).filter_by(spec_version_id=spec.id, audience=audience).one_or_none()
            if existing is not None:
                return existing
            project_id, spec_id, revision, markdown = project.id, spec.id, spec.revision, spec.markdown

        plan, pptx = await self._compose(revision, markdown, audience, stage="frozen")
        digest = hashlib.sha256(pptx).hexdigest()
        with self._session_factory() as db:
            project = db.get(Project, project_id)
            if project is None or project.current_spec_version_id != spec_id:
                raise ValueError("生成期间 PRD 版本已变化，请重新生成。")
            existing = db.query(PrdPresentation).filter_by(spec_version_id=spec_id, audience=audience).one_or_none()
            if existing is not None:
                return existing
            presentation = PrdPresentation(
                id=uuid4().hex, project_id=project_id, spec_version_id=spec_id,
                audience=audience, title=plan.title, plan=plan.model_dump(mode="json"),
                pptx=pptx, content_hash=digest,
            )
            db.add(presentation)
            db.add(AuditEvent(
                id=uuid4().hex, project_id=project_id, session_id=session_id,
                event_type="PRD_PRESENTATION_GENERATED", actor_id=actor_id,
                payload={"spec_version_id": spec_id, "presentation_id": presentation.id,
                         "audience": audience, "content_hash": digest,
                         "skills": ["ppt-generation", "officecli-skill"]},
            ))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.query(PrdPresentation).filter_by(spec_version_id=spec_id, audience=audience).one_or_none()
                if existing is None:
                    raise
                return existing
            return presentation

    async def _compose(
        self, revision: int, markdown: str, audience: Audience, *, stage: str
    ) -> tuple[PresentationPlan, bytes]:
        """Run the ppt-generation Skill and render the plan — shared by both routes."""

        prompt = build_node_prompt(objective=_presentation_objective(audience, stage), input_payload={
            "audience": audience, "prd_revision": revision, "prd_markdown": markdown[:120_000],
            **({"stage": stage} if stage != "frozen" else {}),
        })
        source = _source_text(markdown)

        def validate_plan(plan: PresentationPlan) -> None:
            citations = [slide.source_quote for slide in plan.slides if slide.source_quote.strip()]
            if len(citations) < len(plan.slides) // 2 or any(_source_text(quote) not in source for quote in citations):
                raise OutputConsistencyError([{
                    "code": "PPT_SOURCE_EVIDENCE",
                    "path": "slides.source_quote",
                    "message": "Substantive slides need exact quotes copied from the supplied PRD.",
                }])

        plan = await CodexStructuredRunner(self._settings).run(
            prompt, PresentationPlan, self._settings.codex_cwd,
            bundled_skills=("ppt-generation",),
            validate_output=validate_plan,
            # Presentations are a reusable derived artifact: one repair pass beats the
            # default two, since a re-run is cheap for the caller to trigger.
            max_attempts=2,
        )
        pptx = await asyncio.to_thread(_render_editable_pptx, plan, audience, stage)
        return plan, pptx

    async def generate_draft_payload(
        self,
        project_id: str,
        *,
        document_id: str,
        revision: int,
        markdown: str,
        audience: Audience,
        actor_id: str | None = None,
    ) -> DraftPresentationResult:
        """Build an editable PPTX from an **unfrozen** PRD draft.

        Uses the exact same Agent/skill route as the frozen presentation
        (:meth:`_compose`, ``ppt-generation`` + ``officecli-skill``); only the
        source differs: the current block document draft instead of an immutable
        ``SpecVersion``. The bytes are returned to the caller, which persists them
        as a document artifact keyed by document revision; no ``PrdPresentation``
        row is written and the freeze invariants are untouched.
        """

        if not markdown.strip():
            raise ValueError("当前 PRD 草稿还没有正文，无法生成演示 PPT。")
        plan, pptx = await self._compose(revision, markdown, audience, stage="draft")
        digest = hashlib.sha256(pptx).hexdigest()
        with self._session_factory() as db:
            project = db.get(Project, project_id)
            db.add(AuditEvent(
                id=uuid4().hex, project_id=project_id,
                session_id=project.session_id if project is not None else None,
                event_type="PRD_PRESENTATION_GENERATED", actor_id=actor_id,
                payload={"document_id": document_id, "document_revision": revision,
                         "audience": audience, "stage": "draft", "content_hash": digest,
                         "skills": ["ppt-generation", "officecli-skill"]},
            ))
            db.commit()
        return DraftPresentationResult(
            title=plan.title, pptx=pptx, plan=plan.model_dump(mode="json"),
            preview_html=render_preview_html(plan, pptx, audience, stage="draft"),
        )
