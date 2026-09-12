from sqlalchemy import Column, Integer, String, Text
from app.database.database import Base


class Document(Base):
    __tablename__ = "prd_documents"
    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=False, unique=True)
    title = Column(String, nullable=False)
    background = Column(Text, nullable=False, default="")
    global_rules = Column(Text, nullable=False, default="")
    template = Column(String, nullable=False, default="alibaba")
    plan_status = Column(String, nullable=False, default="pending")
    revision = Column(Integer, nullable=False, default=1)
    context_revision = Column(Integer, nullable=False, default=1)
    source_spec_version_id = Column(String)
    submitted_revision = Column(Integer)
    submitted_spec_id = Column(String)
    created_by = Column(String, nullable=False)
