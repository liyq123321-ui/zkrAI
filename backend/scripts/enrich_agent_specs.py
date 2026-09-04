"""Enrich an approved session's existing task specifications without recreating tasks."""

import argparse
import asyncio
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--actor-id", required=True, help="An existing project owner or reviewer")
    parser.add_argument("--revised-plans", type=Path, help="UTF-8 JSON map of every task key to its corrected ImplementationPlan")
    parser.add_argument("--source-review-call-id", help="Rejected review bound to the unchanged current task snapshot")
    args = parser.parse_args()
    if (args.revised_plans is None) != (args.source_review_call_id is None):
        parser.error("--revised-plans and --source-review-call-id must be supplied together")
    plans = None
    if args.revised_plans is not None:
        try:
            plans = json.loads(args.revised_plans.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            parser.error(f"Cannot read revised plans: {error}")
        if not isinstance(plans, dict):
            parser.error("--revised-plans must contain a JSON object mapping task keys to plans")
    backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend))
    from dotenv import load_dotenv

    load_dotenv(backend / ".env")
    from app.agents.codex import CodexAgentGateway
    from app.config import Settings
    from app.database.database import create_engine_for_url, make_session_factory
    from app.database.models import Project
    from app.services.agent_spec_details import AgentSpecDetailService

    settings = Settings.from_env()
    engine = create_engine_for_url(settings.database_url)
    sessions = make_session_factory(engine)
    try:
        with sessions() as db:
            project = db.query(Project).filter_by(session_id=args.session_id).one()
            project_id = project.id
        service = AgentSpecDetailService(sessions, CodexAgentGateway(settings=settings))
        if plans is not None:
            print("正在独立复审提交的完整修正方案；全部通过后统一保存。", flush=True)
            specs = asyncio.run(service.review_revised_plans(project_id, args.actor_id, args.source_review_call_id, plans))
        else:
            print("正在生成并复审现有任务的实现方案；全部通过后统一保存。", flush=True)
            specs = asyncio.run(service.enrich(project_id, args.actor_id))
        print(json.dumps({"session_id": args.session_id, "agent_specs": len(specs),
                          "agent_spec_ids": [spec.id for spec in specs]}, ensure_ascii=False), flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
