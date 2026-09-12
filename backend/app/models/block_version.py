from sqlalchemy import Column, Integer, JSON, String, DateTime, UniqueConstraint
from app.database.database import Base
from app.database.models import utc_now


class BlockVersion(Base):
    __tablename__ = "prd_block_versions"
    __table_args__ = (UniqueConstraint("block_id", "version"),)
    id = Column(String, primary_key=True)
    document_id = Column(String, nullable=False, index=True)
    block_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    source = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    run_id = Column(String)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
