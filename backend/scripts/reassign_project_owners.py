"""Reassign the standard SDLC work-item tree and matching Agent Specs atomically."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
import sys
from uuid import uuid4


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Commit the reassignment; otherwise only print the plan")
    parser.add_argument("--project-owner", default="owner-1")
    parser.add_argument("--frontend-owner", default="owner-2")
    parser.add_argument("--backend-owner", default="owner-3")
    parser.add_argument("--design-owner", default="owner-4")
    parser.add_argument("--qa-owner", default="owner-5")
    args = parser.parse_args()

    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    from dotenv import load_dotenv

    load_dotenv(backend / ".env")
    os.chdir(backend)
    from app.config import Settings
    from app.database.database import create_engine_for_url, make_session_factory
    from app.database.models import AgentSpec, AuditEvent, Project, WorkItem

    assignments = {
        "root": (args.project_owner, "项目负责人 / 项目经理"),
        "ms-requirements": (args.project_owner, "项目经理"),
        "ms-design": (args.design_owner, "设计负责人"),
        "ms-implementation": (args.frontend_owner, "前端负责人"),
        "ms-acceptance": (args.qa_owner, "测试与校验负责人"),
        "ms-delivery": (args.project_owner, "项目经理"),
        "ms-operations": (args.project_owner, "项目负责人"),
        "task-requirements-work": (args.project_owner, "项目经理"),
        "task-design-work": (args.design_owner, "设计负责人"),
        "task-implementation-work": (args.frontend_owner, "前端负责人"),
        "task-acceptance-work": (args.qa_owner, "测试与校验负责人"),
        "task-delivery-work": (args.project_owner, "项目经理"),
        "task-operations-work": (args.project_owner, "项目负责人"),
        "gate-requirements": (args.project_owner, "最终审批人"),
        "gate-design": (args.project_owner, "最终审批人"),
        "gate-implementation": (args.project_owner, "最终审批人"),
        "gate-acceptance": (args.project_owner, "最终审批人"),
        "gate-delivery": (args.project_owner, "最终审批人"),
        "gate-operations": (args.project_owner, "最终审批人"),
    }

    settings = Settings.from_env()
    engine = create_engine_for_url(settings.database_url)
    sessions = make_session_factory(engine)
    try:
        with sessions() as db:
            root = db.get(WorkItem, args.root_id)
            if root is None or root.kind != "ROOT" or not root.project_id:
                raise SystemExit(f"Root WorkItem not found: {args.root_id}")
            project = db.get(Project, root.project_id)
            if project is None:
                raise SystemExit(f"Project not found for root: {args.root_id}")
            items = db.query(WorkItem).filter_by(project_id=project.id).all()
            by_key = {item.local_key: item for item in items}
            missing = sorted(set(assignments) - set(by_key))
            unexpected = sorted(set(by_key) - set(assignments))
            if missing or unexpected:
                raise SystemExit(f"Unexpected SDLC work-item shape; missing={missing}, unexpected={unexpected}")

            changes = []
            for key, (assignee, role) in assignments.items():
                item = by_key[key]
                if item.suggested_assignee != assignee or item.responsible_role != role:
                    changes.append({
                        "work_item_id": item.id,
                        "local_key": key,
                        "from": {"suggested_assignee": item.suggested_assignee, "responsible_role": item.responsible_role},
                        "to": {"suggested_assignee": assignee, "responsible_role": role},
                    })
                item.suggested_assignee = assignee
                item.responsible_role = role

            specs = db.query(AgentSpec).filter_by(project_id=project.id).all()
            item_by_id = {item.id: item for item in items}
            unknown_specs = sorted(spec.work_item_id for spec in specs if spec.work_item_id not in item_by_id)
            if unknown_specs:
                raise SystemExit(f"Agent Specs reference unknown WorkItems: {unknown_specs}")
            agent_spec_changes = 0
            for spec in specs:
                item = item_by_id[spec.work_item_id]
                content = dict(spec.content)
                if (content.get("suggested_assignee") != item.suggested_assignee
                        or content.get("responsible_role") != item.responsible_role):
                    content["suggested_assignee"] = item.suggested_assignee
                    content["responsible_role"] = item.responsible_role
                    spec.content = content
                    spec.content_hash = canonical_hash(content)
                    agent_spec_changes += 1

            project_roles_changed = (
                project.final_approver != args.project_owner
                or project.project_manager_ids != [args.project_owner]
                or project.root_owner_ids != [args.project_owner]
            )
            project.final_approver = args.project_owner
            project.project_manager_ids = [args.project_owner]
            project.root_owner_ids = [args.project_owner]
            prior_state_version = project.state_version
            if not changes and not agent_spec_changes and not project_roles_changed:
                db.rollback()
                print(json.dumps({
                    "mode": "already-current",
                    "project_id": project.id,
                    "root_id": root.id,
                    "state_version": prior_state_version,
                }, ensure_ascii=False, indent=2))
                return
            project.state_version += 1
            project.updated_at = datetime.now(UTC)

            summary = {
                "project_id": project.id,
                "root_id": root.id,
                "work_items": len(items),
                "agent_specs": len(specs),
                "changed_work_items": len(changes),
                "changed_agent_specs": agent_spec_changes,
                "prior_state_version": prior_state_version,
                "new_state_version": project.state_version,
                "assignments": changes,
                "backend_owner_unused": args.backend_owner,
                "backend_owner_reason": "该项目批准范围明确不使用后端、数据库或网络请求",
            }
            if not args.apply:
                db.rollback()
                print(json.dumps({"mode": "dry-run", **summary}, ensure_ascii=False, indent=2))
                return

            db.add(AuditEvent(
                id=str(uuid4()), project_id=project.id, session_id=project.session_id,
                event_type="WORK_ITEM_OWNERS_REASSIGNED", actor_id=args.project_owner,
                payload=summary,
            ))
            db.commit()
            print(json.dumps({"mode": "applied", **summary}, ensure_ascii=False, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
