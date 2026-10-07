"""Review aids and alternative drafts, separate from the canonical document."""
from sqlalchemy import Column, ForeignKey, Integer, JSON, String
from app.database.database import Base


class DocumentWorkspace(Base):
    __tablename__ = "prd_document_workspaces"
    document_id = Column(String, ForeignKey("prd_documents.id"), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    state = Column(JSON, nullable=False, default=dict)
