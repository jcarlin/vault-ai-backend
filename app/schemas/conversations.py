from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    role: str = Field(description="Message role: user, assistant, or system")
    content: str
    thinking_content: str | None = None
    thinking_duration_ms: int | None = None


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    timestamp: float  # Unix epoch ms for frontend compatibility
    thinking: dict | None = None  # {"content": str, "durationMs": int}


class ConversationCreate(BaseModel):
    title: str = "New Conversation"
    model_id: str


class ConversationUpdate(BaseModel):
    title: str | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    model_id: str
    created_at: float
    updated_at: float
    message_count: int = 0


class ConversationResponse(BaseModel):
    id: str
    title: str
    model_id: str
    messages: list[MessageResponse]
    created_at: float
    updated_at: float
