"""Create editable, version-bound presentations from a frozen PRD."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
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


def _source_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _checked_officecli() -> Path:
    root = BUNDLED_SKILL_ROOT / "officecli-skill"
    manifest = json.loads((BUNDLED_SKILL_ROOT / "officecli-skill-source.json").read_text(encoding="utf-8"))
    if manifest.get("name") != "officecli-skill" or _bundled_skill_digest(root) != manifest.get("tree_sha256"):
        raise RuntimeError("officecli-skill integrity check failed")
    binary = root / ("officecli.exe" if os.name == "nt" else "officecli")
    if not binary.is_file():
        raise RuntimeError("officecli is not available on this host")
    return binary


def _render_editable_pptx(plan: PresentationPlan, audience: Audience) -> bytes:
    binary = _checked_officecli()
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

        def office(*args: str) -> None:
            result = subprocess.run(
                [str(binary), *args], cwd=directory, env=env,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"officecli {args[0]} failed: {(result.stderr or result.stdout)[-400:]}")

        def shape(slide: int, text: str, x: float, y: float, width: float, height: float,
                  size: int, color: str, *, fill: str = "none", bold: bool = False) -> None:
            office("add", str(output), f"/slide[{slide}]", "--type", "shape",
                   "--prop", f"text={text}", "--prop", f"x={x}in", "--prop", f"y={y}in",
                   "--prop", f"width={width}in", "--prop", f"height={height}in",
                   "--prop", f"size={size}pt", "--prop", f"color={color}",
                   "--prop", f"fill={fill}", "--prop", f"bold={str(bold).lower()}",
                   "--prop", "font=Microsoft YaHei", "--prop", "autoFit=shrink")

        office("create", str(output))
        total = len(plan.slides) + 1
        for index in range(1, total + 1):
            office("add", str(output), "/", "--type", "slide", "--prop", f"background={background}")
            if index == 1:
                shape(index, "PRODUCT STORY  /  " + ("团队沟通" if dark else "产品介绍"),
                      .72, .65, 11.3, .35, 13, accent, bold=True)
                shape(index, plan.title, .72, 1.65, 11.6, 1.45, 42, title_color, bold=True)
                shape(index, plan.subtitle, .78, 3.45, 10.8, 1.0, 22, body_color)
                shape(index, "基于已冻结 PRD · 可编辑演示文稿", .78, 6.68, 9.5, .32, 11, body_color)
                continue
            slide = plan.slides[index - 2]
            shape(index, f"{index - 1:02d}  /  {slide.kicker}", .72, .48, 11.4, .34, 12, accent, bold=True)
            shape(index, slide.title, .72, 1.02, 11.5, .82, 31, title_color, bold=True)
            shape(index, slide.takeaway, .75, 1.9, 11.1, .7, 18, body_color)
            for bullet_index, bullet in enumerate(slide.bullets[:4]):
                y = 2.83 + bullet_index * .88
                shape(index, "●", .83, y + .1, .26, .3, 14, accent)
                shape(index, bullet, 1.19, y, 10.95, .69, 18, title_color,
                      fill=card if bullet_index == 0 else "none")
            shape(index, f"PRD  ·  {index - 1}/{len(plan.slides)}", .78, 6.72, 10.8, .3, 10, body_color)
            if slide.speaker_notes:
                office("set", str(output), f"/slide[{index}]", "--prop", f"notes={slide.speaker_notes}")
        office("validate", str(output))
        return output.read_bytes()


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

        objective = (
            "You are a product manager preparing an editable presentation from a frozen PRD. "
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
        prompt = build_node_prompt(objective=objective, input_payload={
            "audience": audience, "prd_revision": revision, "prd_markdown": markdown[:120_000],
        })
        source = _source_text(markdown)

        def validate_plan(plan: PresentationPlan) -> None:
            citations = [slide.source_quote for slide in plan.slides if slide.source_quote.strip()]
            if len(citations) < len(plan.slides) // 2 or any(_source_text(quote) not in source for quote in citations):
                raise OutputConsistencyError([{
                    "code": "PPT_SOURCE_EVIDENCE",
                    "path": "slides.source_quote",
                    "message": "Substantive slides need exact quotes copied from the frozen PRD.",
                }])

        plan = await CodexStructuredRunner(self._settings).run(
            prompt, PresentationPlan, self._settings.codex_cwd,
            bundled_skills=("ppt-generation",),
            validate_output=validate_plan,
        )
        pptx = await asyncio.to_thread(_render_editable_pptx, plan, audience)
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
