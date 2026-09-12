from sqlalchemy import Boolean, Column, JSON, String, Text, DateTime
from app.database.database import Base
from app.database.models import utc_now


class BlockComment(Base):
    __tablename__ = "prd_block_comments"
    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False, index=True)
    text = Column(Text, nullable=False)
    targets = Column(JSON, nullable=False)
    actor_id = Column(String, nullable=False)
    resolved = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
