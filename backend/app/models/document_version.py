from sqlalchemy import Column, DateTime, Integer, JSON, String, UniqueConstraint
from app.database.database import Base
from app.database.models import utc_now


class DocumentVersion(Base):
    __tablename__ = "prd_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    source = Column(String, nullable=False)
    snapshot = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
