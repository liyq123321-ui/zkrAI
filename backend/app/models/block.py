from sqlalchemy import Column, Integer, JSON, String, Text, ForeignKey
from app.database.database import Base


class Block(Base):
    __tablename__ = "prd_blocks"
    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("prd_documents.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    parent_id = Column(String)
    order = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    version = Column(Integer, nullable=False, default=1)
    content = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=False, default="")
    instruction = Column(Text, nullable=False, default="")
    dependencies = Column(JSON, nullable=False, default=list)
    fragment = Column(JSON)
    fragment_version = Column(Integer)
