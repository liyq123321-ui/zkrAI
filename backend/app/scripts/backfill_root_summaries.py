"""Backfill concise summaries for existing project ROOT work items."""

import asyncio
import json
from dataclasses import asdict

from app.agents.codex import CodexAgentGateway
from app.config import Settings
from app.database.database import SessionLocal, engine, init_database
from app.services.project_summary_backfill import ProjectSummaryBackfill


async def run() -> int:
    settings = Settings.from_env()
    init_database(engine)
    result = await ProjectSummaryBackfill(
        SessionLocal, CodexAgentGateway(settings=settings)
    ).run()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
