"""Transactional block editing on the existing SQLite database."""
import hashlib
import json
from datetime import UTC, datetime
from contextlib import contextmanager
from uuid import uuid4
from sqlalchemy import text, update
from app.database.models import Project, SpecVersion, SpecReview, AgentSession, AgentCall, AuditEvent
from app.models import Document, Block, BlockVersion, BlockComment, AgentRun
from app.ai.provider import BlockPlan, PlanNode, SpecFragment
from app.domain.types import ProjectSpecPayload
from app.services.prd_templates import TEMPLATES, parse_outline, validate_plan
from app.services.spec_review import run_rule_review


def uid(prefix):
    return f"{prefix}_{uuid4().hex}"


def record(row):
    result = {col.name: getattr(row, col.name) for col in row.__table__.columns}
    for key, value in result.items():
        if isinstance(value, datetime):
            result[key] = (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return result


class BlockError(Exception):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


class BlockService:
    def __init__(self, session_factory):
        self.factory = session_factory

    @contextmanager
    def write(self):
        with self.factory() as db:
            # Acquire the SQLite writer lock before reading any version. Every write,
            # history insert and invalidation below shares this transaction.
            if db.get_bind().dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def document(self, db, document_id, actor):
        doc = db.get(Document, document_id)
        if doc is None:
            raise BlockError("文档不存在", 404)
        self.authorize(db, doc.project_id, actor)
        return doc

    def authorize(self, db, project_id, actor):
        project = db.get(Project, project_id)
        if project is None:
            raise BlockError("项目不存在", 404)
        if actor not in {project.final_approver, *(project.project_manager_ids or []), *(project.root_owner_ids or [])}:
            raise BlockError("当前身份没有此项目的编辑权限", 403)
        return project

    def block(self, db, doc, block_id):
        block = db.get(Block, block_id)
        if block is None or block.document_id != doc.id:
            raise BlockError("Block 不属于此文档", 404)
        return block

    def blocks(self, db, doc):
        return db.query(Block).filter_by(document_id=doc.id).order_by(Block.order).all()

    def history(self, db, block, source, actor, run_id=None):
        db.add(BlockVersion(id=uid("version"), document_id=block.document_id, block_id=block.id,
                            version=block.version, snapshot=record(block), source=source,
                            actor_id=actor, run_id=run_id))

    def replace_plan(self, db, doc, plan, actor):
        try:
            validate_plan(plan)
        except ValueError as error:
            raise BlockError(str(error), 422) from error
        existing = self.blocks(db, doc)
        if any(b.content or b.version > 1 for b in existing):
            raise BlockError("已有内容或编辑历史，不能用新大纲覆盖；请保留当前草稿")
        if db.query(BlockComment).filter_by(document_id=doc.id).count():
            raise BlockError("已有批注，不能替换其关联的大纲")
        for block in existing:
            db.query(BlockVersion).filter_by(block_id=block.id).delete()
            db.delete(block)
        ids = {node.key: uid("block") for node in plan.blocks}
        for order, node in enumerate(plan.blocks):
            block = Block(id=ids[node.key], document_id=doc.id, title=node.title,
                          parent_id=ids.get(node.parent_key), order=order, status="pending", version=1,
                          content="", summary="", instruction=node.instruction,
                          dependencies=[ids[key] for key in node.dependencies])
            db.add(block)
            db.flush()
            self.history(db, block, "outline", actor)
        doc.revision += 1

    def open(self, session_id, actor):
        with self.write() as db:
            project = db.query(Project).filter_by(session_id=session_id).one_or_none()
            if project is None:
                raise BlockError("项目不存在", 404)
            self.authorize(db, project.id, actor)
            doc = db.query(Document).filter_by(session_id=session_id).one_or_none()
            if doc is None:
                doc = Document(id=uid("prd"), project_id=project.id, session_id=session_id,
                               title=str(project.brief.get("title") or project.brief.get("project_name") or project.brief.get("final_objective") or "PRD 文档")[:100],
                               background=json.dumps(project.brief, ensure_ascii=False), global_rules="以已确认需求为准；不虚构业务事实、批准或完成状态。",
                               template="alibaba", plan_status="pending", revision=1, context_revision=1,
                               created_by=actor, source_spec_version_id=project.current_spec_version_id)
                db.add(doc)
                db.flush()
                spec = db.get(SpecVersion, project.current_spec_version_id) if project.current_spec_version_id else None
                if spec:
                    self.import_spec(db, doc, spec, actor)
                else:
                    self.replace_plan(db, doc, parse_outline(TEMPLATES["alibaba"]["outline"]), actor)
            document_id = doc.id
        return self.snapshot(document_id, actor)

    def import_spec(self, db, doc, spec, actor):
        # Keep original body text and diagram references. Headings live in the
        # tree, so export emits each heading once instead of duplicating it.
        import re
        chunks, title, lines, depth = [], "文档说明", [], 1
        fence = None
        saw_heading = False
        for line in spec.markdown.splitlines(keepends=True):
            marker = re.match(r'^\s*(`{3,}|~{3,})', line)
            if marker:
                token = marker.group(1)[0]
                fence = None if fence == token else token if fence is None else fence
            heading = re.match(r'^(#{1,6})\s+(.+?)\s*$', line) if not fence else None
            if heading:
                if lines or saw_heading:
                    chunks.append((depth, title, ''.join(lines)))
                title, lines, depth = heading.group(2), [], len(heading.group(1))
                saw_heading = True
            else:
                lines.append(line)
        if lines or saw_heading:
            chunks.append((depth, title, ''.join(lines)))
        parents = []
        for index, (depth, title, content) in enumerate(chunks):
            while parents and parents[-1][0] >= depth:
                parents.pop()
            block_id = uid("block")
            key = re.sub(r'[\s-]+', '_', title.casefold())
            if key == "source_references":
                key = "source_refs"
            fragment = None
            if key in ProjectSpecPayload.model_fields and key != "er_diagrams":
                fragment = SpecFragment.model_validate({key: spec.content.get(key, []),
                    "er_diagrams": [d for d in spec.content.get("er_diagrams", []) if d.get("after_section") == key]
                }).model_dump(mode="json")
            block = Block(id=block_id, document_id=doc.id, title=title, order=index,
                          parent_id=parents[-1][1] if parents else None,
                          status="ready" if content.strip() else "pending",
                          version=1, content=content, summary=content[:2000], instruction="保留来源 PRD 的需求 ID 与图引用。",
                          dependencies=[], fragment=fragment, fragment_version=1 if fragment is not None else None)
            db.add(block)
            db.flush()
            self.history(db, block, "import", actor)
            parents.append((depth, block.id))
        doc.plan_status = "ready"
        doc.template = "existing"

    def snapshot(self, document_id, actor):
        with self.factory() as db:
            doc = self.document(db, document_id, actor)
            runs = db.query(AgentRun).filter_by(document_id=doc.id).order_by(AgentRun.created_at).all()
            # Do not expose prompt snapshots through routine polling.
            return {"document": record(doc), "blocks": [record(b) for b in self.blocks(db, doc)],
                    "comments": [record(c) for c in db.query(BlockComment).filter_by(document_id=doc.id).all()],
                    "runs": [{k: v for k, v in record(r).items() if k != "input_snapshot"} for r in runs]}

    def import_outline(self, document_id, actor, template, outline, expected_revision):
        with self.write() as db:
            doc = self.document(db, document_id, actor)
            if doc.revision != expected_revision:
                raise BlockError("文档已改变，请刷新后重试")
            if db.query(AgentRun).filter_by(document_id=doc.id).filter(AgentRun.status.in_(["queued", "running"])).count():
                raise BlockError("请先取消运行中的 AI 作业")
            if template in TEMPLATES:
                outline = TEMPLATES[template]["outline"]
            try:
                plan = parse_outline(outline)
            except ValueError as error:
                raise BlockError(str(error), 422) from error
            self.replace_plan(db, doc, plan, actor)
            doc.template, doc.plan_status = template, "pending"
        return self.snapshot(document_id, actor)

    def invalidate(self, db, doc, upstream):
        blocks, changed = self.blocks(db, doc), set(upstream)
        while True:
            affected = {b.id for b in blocks if b.id not in changed and set(b.dependencies) & changed}
            if not affected:
                break
            changed |= affected
        for b in blocks:
            if b.id in changed - set(upstream) and b.content:
                b.status = "outdated"

    def save_in_transaction(self, db, doc, block, expected_version, values, actor, source="manual", run_id=None):
        if block.version != expected_version:
            raise BlockError("Block 已有新版本；你的输入已保留，请比较后再保存")
        if all(getattr(block, key) == value for key, value in values.items()):
            return block
        from app.domain.types import ErDiagram
        from app.services.drawio_diagrams import diagram_anchor
        preserved = [diagram for diagram in (block.fragment or {}).get("er_diagrams", [])
                     if diagram_anchor(ErDiagram.model_validate(diagram)) in values.get("content", block.content)]
        if "fragment" in values and preserved:
            fragment = dict(values["fragment"])
            supplied_ids = {d["diagram_id"] for d in fragment.get("er_diagrams", [])}
            fragment["er_diagrams"] = [*fragment.get("er_diagrams", []), *(d for d in preserved if d["diagram_id"] not in supplied_ids)]
            values = {**values, "fragment": fragment}
        next_version = expected_version + 1
        changed = db.execute(update(Block).where(Block.id == block.id, Block.version == expected_version)
                             .values(**values, version=next_version).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise BlockError("Block 版本冲突")
        db.expire(block)
        block.status = "ready" if block.content else "pending"
        if "fragment" not in values:
            block.fragment = {"er_diagrams": preserved} if preserved else None
            block.fragment_version = None
            block.summary = block.content[:2000]
        else:
            block.fragment_version = next_version
        db.flush()
        self.history(db, block, source, actor, run_id)
        self.invalidate(db, doc, [block.id])
        doc.revision += 1
        return block

    def save(self, document_id, block_id, actor, expected_version, values):
        with self.write() as db:
            doc = self.document(db, document_id, actor)
            block = self.block(db, doc, block_id)
            if "dependencies" in values or "parent_id" in values:
                nodes = [PlanNode(key=b.id, title=b.title, parent_key=values.get("parent_id", b.parent_id) if b.id == block_id else b.parent_id,
                                  instruction=b.instruction, dependencies=values.get("dependencies", b.dependencies) if b.id == block_id else b.dependencies)
                         for b in self.blocks(db, doc)]
                try:
                    validate_plan(BlockPlan(blocks=nodes))
                except ValueError as error:
                    raise BlockError(str(error), 422) from error
            self.save_in_transaction(db, doc, block, expected_version, values, actor)
        return self.snapshot(document_id, actor)

    def context(self, document_id, actor, expected_revision, background, global_rules):
        with self.write() as db:
            doc = self.document(db, document_id, actor)
            if doc.revision != expected_revision:
                raise BlockError("文档已改变，请刷新后重试")
            doc.background, doc.global_rules = background, global_rules
            doc.context_revision += 1
            doc.revision += 1
            for block in self.blocks(db, doc):
                if block.content:
                    block.status = "outdated"
        return self.snapshot(document_id, actor)

    def versions(self, document_id, block_id, actor):
        with self.factory() as db:
            doc = self.document(db, document_id, actor)
            self.block(db, doc, block_id)
            return [record(v) for v in db.query(BlockVersion).filter_by(block_id=block_id).order_by(BlockVersion.version.desc())]

    def restore(self, document_id, block_id, actor, version, expected_version):
        with self.write() as db:
            doc = self.document(db, document_id, actor)
            block = self.block(db, doc, block_id)
            old = db.query(BlockVersion).filter_by(block_id=block_id, version=version).one_or_none()
            if not old:
                raise BlockError("历史版本不存在", 404)
            # Restoring text never silently restores obsolete graph references.
            values = {key: old.snapshot[key] for key in ("title", "content", "instruction")}
            self.save_in_transaction(db, doc, block, expected_version, values, actor, "restore")
        return self.snapshot(document_id, actor)

    def comment(self, document_id, actor, body, targets):
        with self.write() as db:
            doc = self.document(db, document_id, actor)
            frozen = []
            if len({t["block_id"] for t in targets}) != len(targets):
                raise BlockError("同一批注的 Block 目标不可重复", 422)
            for target in targets:
                b = self.block(db, doc, target["block_id"])
                if target["base_version"] != b.version:
                    raise BlockError("批注目标已改变，请重新选择范围")
                start, end = target.get("start_line"), target.get("end_line")
                lines = b.content.splitlines(keepends=True)
                if (start is None) != (end is None) or (start is not None and not 1 <= start <= end <= len(lines)):
                    raise BlockError("批注行范围无效", 422)
                frozen.append({**target, "quote": ''.join(lines[start - 1:end]) if start else b.content[:2000]})
            db.add(BlockComment(id=uid("comment"), document_id=doc.id, text=body, targets=frozen, actor_id=actor))
            doc.revision += 1
        return self.snapshot(document_id, actor)

    def resolve(self, document_id, comment_id, actor, resolved):
        with self.write() as db:
            doc = self.document(db, document_id, actor)
            comment = db.get(BlockComment, comment_id)
            if not comment or comment.document_id != doc.id:
                raise BlockError("批注不存在", 404)
            comment.resolved = resolved
            doc.revision += 1
        return self.snapshot(document_id, actor)

    def markdown(self, blocks):
        by_parent = {}
        for block in blocks:
            by_parent.setdefault(block.parent_id, []).append(block)
        lines = []
        def walk(parent, depth):
            for b in by_parent.get(parent, []):
                lines.append('#' * min(depth, 6) + ' ' + b.title + '\n\n' + b.content)
                walk(b.id, depth + 1)
        walk(None, 1)
        return '\n\n'.join(lines) + '\n'

    def submit(self, document_id, actor, expected_revision):
        with self.write() as db:
            doc = self.document(db, document_id, actor)
            if doc.revision != expected_revision:
                raise BlockError("文档已改变，请刷新再提交")
            if doc.submitted_revision == doc.revision:
                return {"spec_version_id": doc.submitted_spec_id}
            project = self.authorize(db, doc.project_id, actor)
            if project.phase not in ("SPECIFICATION", "REVIEW"):
                raise BlockError("当前项目阶段不允许替换 PRD；请先完成需求澄清或返回评审阶段")
            if project.current_spec_version_id != (doc.submitted_spec_id or doc.source_spec_version_id):
                raise BlockError("现有流程产生了更新的 PRD，请先核对新基线")
            if db.query(AgentRun).filter_by(document_id=doc.id).filter(AgentRun.status.in_(["queued", "running"])).count():
                raise BlockError("请等待或取消当前 AI 作业")
            blocks = self.blocks(db, doc)
            containers = {b.parent_id for b in blocks if b.parent_id}
            body_blocks = [b for b in blocks if b.content.strip() or b.id not in containers]
            missing = [b.title for b in body_blocks if not b.content.strip() or b.status == "outdated" or b.fragment_version != b.version]
            if missing:
                raise BlockError("请先完成内容并审核这些 Block：" + "、".join(missing))
            merged = {key: [] for key in ProjectSpecPayload.model_fields}
            for b in body_blocks:
                fragment = SpecFragment.model_validate(b.fragment).model_dump(mode="json")
                for key, values in fragment.items():
                    if key == "source_refs":
                        continue
                    for value in values:
                        if value not in merged[key]:
                            merged[key].append(value)
            refs = [f"block:{b.id}:v{b.version}" for b in blocks]
            merged["source_refs"] = refs
            try:
                payload = ProjectSpecPayload.model_validate(merged)
            except ValueError as error:
                raise BlockError("Block 结构化内容尚不完整：" + str(error)[:1800]) from error
            findings = run_rule_review(payload, set(refs))
            if findings:
                raise BlockError("规格校验未通过：" + "；".join(f"{f.spec_path}: {f.message}" for f in findings)[:3000])
            content = payload.model_dump(mode="json")
            digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            agent_session_id, call_id, version_id = uid("session"), uid("call"), uid("spec")
            db.add(AgentSession(id=agent_session_id, project_id=project.id, role="PM", purpose="Block PRD snapshot"))
            db.add(AgentCall(id=call_id, project_id=project.id, agent_session_id=agent_session_id, operation="block_snapshot",
                             status="SUCCEEDED", request={"input_refs": refs}, response=content))
            latest = db.query(SpecVersion.revision).filter_by(project_id=project.id).order_by(SpecVersion.revision.desc()).first()
            version = SpecVersion(id=version_id, project_id=project.id, revision=(latest[0] if latest else 0) + 1,
                                 content=content, markdown=self.markdown(blocks), generation_source="BLOCK_EDITOR",
                                 input_refs=refs, generator_agent_session_id=agent_session_id, generator_call_id=call_id,
                                 parent_version_id=project.current_spec_version_id, change_summary="提交 Block 文档快照",
                                 content_hash=digest, status="HUMAN_REVIEW")
            db.add(version)
            db.add(SpecReview(id=uid("review"), project_id=project.id, spec_version_id=version_id, kind="RULE",
                             reviewer_id="SYSTEM", input_spec_hash=digest, verdict="PASS", findings=[]))
            # Local extraction is not a fictitious full-document semantic review receipt.
            db.add(AuditEvent(id=uid("audit"), project_id=project.id, session_id=project.session_id,
                              actor_id=actor, event_type="BLOCK_PRD_SUBMITTED", payload={"spec_version_id": version_id, "input_refs": refs}))
            project.current_spec_version_id, project.phase = version_id, "REVIEW"
            project.state_version += 1
            doc.submitted_revision, doc.submitted_spec_id = doc.revision, version_id
        return {"spec_version_id": version_id}
