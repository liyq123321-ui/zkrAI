from pydantic import BaseModel, Field


class ChatRequest(BaseModel):

    session_id: str | None = None

    message: str

    workflow: str = "spec_only"

    agent: str = "planner"

    actor_id: str | None = Field(default="legacy-user", min_length=1)
