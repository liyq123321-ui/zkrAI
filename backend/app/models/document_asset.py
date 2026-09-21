from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from app.database.database import Base
from app.database.models import utc_now


class DocumentAsset(Base):
    __tablename__ = "prd_document_assets"

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("prd_documents.id"), nullable=False, index=True)
    block_id = Column(String, ForeignKey("prd_blocks.id"), nullable=True, index=True)
    type = Column(String, nullable=False)
    storage_key = Column(String, nullable=False, unique=True)
    file_name = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    width = Column(Integer)
    height = Column(Integer)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)


class DocumentArtifact(Base):
    __tablename__ = "prd_document_artifacts"
    __table_args__ = (
        UniqueConstraint("document_id", "revision", "kind", name="uq_prd_document_artifact_revision"),
    )

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("prd_documents.id"), nullable=False, index=True)
    revision = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)
    storage_key = Column(String, nullable=False, unique=True)
    file_name = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
