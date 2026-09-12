from sqlalchemy import Column, Integer, JSON, String, Text, DateTime, Index, UniqueConstraint, text
from app.database.database import Base
from app.database.models import utc_now


class AgentRun(Base):
    __tablename__ = "prd_agent_runs"
    __table_args__ = (
        UniqueConstraint("document_id", "request_id"),
        Index("uq_prd_active_block", "block_id", unique=True,
              sqlite_where=text("status IN ('queued', 'running')")),
        Index("uq_prd_active_plan", "document_id", unique=True,
              sqlite_where=text("type = 'plan' AND status IN ('queued', 'running')")),
    )
    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False, index=True)
    block_id = Column(String)
    type = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    base_version = Column(Integer, nullable=False)
    input_snapshot = Column(JSON, nullable=False)
    dependency_versions = Column(JSON, nullable=False, default=dict)
    context_revision = Column(Integer, nullable=False)
    result = Column(JSON)
    apply_status = Column(String, nullable=False, default="pending")
    error = Column(Text)
    request_id = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
