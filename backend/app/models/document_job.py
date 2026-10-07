from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from app.database.database import Base
from app.database.models import utc_now


class DocumentJob(Base):
    __tablename__ = "prd_document_jobs"
    __table_args__ = (UniqueConstraint("document_id", "request_id", name="uq_prd_job_request"),)
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("prd_documents.id"), nullable=False, index=True)
    actor_id = Column(String, nullable=False)
    request_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    base_revision = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)
    result = Column(JSON)
    events = Column(JSON, nullable=False, default=list)
    error = Column(String)
    apply_fingerprint = Column(String)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
