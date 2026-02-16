from fastapi import APIRouter, Query

from app.schemas.conversations import (
    ConversationCreate,
    ConversationResponse,
    ConversationSummary,
    ConversationUpdate,
    MessageCreate,
    MessageResponse,
)
from app.services.conversations import ConversationService

router = APIRouter()

_service = ConversationService()


@router.get("/vault/conversations")
async def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ConversationSummary]:
    """List all conversations, sorted by most recently updated."""
    return await _service.list_conversations(limit=limit, offset=offset)


@router.post("/vault/conversations", status_code=201)
async def create_conversation(body: ConversationCreate) -> ConversationSummary:
    """Create a new conversation."""
    return await _service.create_conversation(body)


@router.get("/vault/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> ConversationResponse:
    """Get a conversation with all its messages."""
    return await _service.get_conversation(conversation_id)


@router.put("/vault/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str, body: ConversationUpdate
) -> ConversationSummary:
    """Update conversation title."""
    return await _service.update_conversation(conversation_id, body)


@router.delete("/vault/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str) -> None:
    """Delete a conversation and all its messages."""
    await _service.delete_conversation(conversation_id)


@router.post("/vault/conversations/{conversation_id}/messages", status_code=201)
async def add_message(conversation_id: str, body: MessageCreate) -> MessageResponse:
    """Add a message to a conversation."""
    return await _service.add_message(conversation_id, body)
