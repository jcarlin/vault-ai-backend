import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.core.database as db_module
from app.core.database import Conversation, Message
from app.core.exceptions import NotFoundError
from app.schemas.conversations import (
    ConversationCreate,
    ConversationResponse,
    ConversationSummary,
    ConversationUpdate,
    MessageCreate,
    MessageResponse,
)


def _to_epoch_ms(dt: datetime) -> float:
    return dt.timestamp() * 1000


def _message_to_response(msg: Message) -> MessageResponse:
    thinking = None
    if msg.thinking_content:
        thinking = {"content": msg.thinking_content, "durationMs": msg.thinking_duration_ms}
    return MessageResponse(
        id=msg.id,
        role=msg.role,
        content=msg.content,
        timestamp=_to_epoch_ms(msg.timestamp),
        thinking=thinking,
    )


class ConversationService:
    def __init__(self, session_factory: async_sessionmaker | None = None):
        self._session_factory_override = session_factory

    @property
    def _session_factory(self) -> async_sessionmaker:
        return self._session_factory_override or db_module.async_session

    async def list_conversations(self, limit: int = 50, offset: int = 0) -> list[ConversationSummary]:
        async with self._session_factory() as session:
            # Get conversations ordered by updated_at desc
            stmt = (
                select(Conversation)
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
            result = await session.execute(stmt)
            conversations = list(result.scalars().all())

            summaries = []
            for conv in conversations:
                # Count messages for this conversation
                count_stmt = select(func.count()).where(Message.conversation_id == conv.id)
                count_result = await session.execute(count_stmt)
                message_count = count_result.scalar() or 0

                summaries.append(
                    ConversationSummary(
                        id=conv.id,
                        title=conv.title,
                        model_id=conv.model_id,
                        created_at=_to_epoch_ms(conv.created_at),
                        updated_at=_to_epoch_ms(conv.updated_at),
                        message_count=message_count,
                    )
                )
            return summaries

    async def create_conversation(self, data: ConversationCreate) -> ConversationSummary:
        conv = Conversation(
            id=str(uuid.uuid4()),
            title=data.title,
            model_id=data.model_id,
        )
        async with self._session_factory() as session:
            session.add(conv)
            await session.commit()
            await session.refresh(conv)

        return ConversationSummary(
            id=conv.id,
            title=conv.title,
            model_id=conv.model_id,
            created_at=_to_epoch_ms(conv.created_at),
            updated_at=_to_epoch_ms(conv.updated_at),
            message_count=0,
        )

    async def get_conversation(self, conversation_id: str) -> ConversationResponse:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalar_one_or_none()
            if conv is None:
                raise NotFoundError(f"Conversation {conversation_id} not found.")

            msg_result = await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.timestamp.asc())
            )
            messages = list(msg_result.scalars().all())

            return ConversationResponse(
                id=conv.id,
                title=conv.title,
                model_id=conv.model_id,
                messages=[_message_to_response(m) for m in messages],
                created_at=_to_epoch_ms(conv.created_at),
                updated_at=_to_epoch_ms(conv.updated_at),
            )

    async def update_conversation(self, conversation_id: str, data: ConversationUpdate) -> ConversationSummary:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalar_one_or_none()
            if conv is None:
                raise NotFoundError(f"Conversation {conversation_id} not found.")

            update_values = {}
            if data.title is not None:
                update_values["title"] = data.title
            update_values["updated_at"] = datetime.now(timezone.utc)

            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(**update_values)
            )
            await session.commit()

            # Re-fetch to get updated values
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalar_one()

            count_result = await session.execute(
                select(func.count()).where(Message.conversation_id == conv.id)
            )
            message_count = count_result.scalar() or 0

            return ConversationSummary(
                id=conv.id,
                title=conv.title,
                model_id=conv.model_id,
                created_at=_to_epoch_ms(conv.created_at),
                updated_at=_to_epoch_ms(conv.updated_at),
                message_count=message_count,
            )

    async def delete_conversation(self, conversation_id: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalar_one_or_none()
            if conv is None:
                raise NotFoundError(f"Conversation {conversation_id} not found.")

            # Delete messages first (CASCADE should handle this, but be explicit)
            await session.execute(
                delete(Message).where(Message.conversation_id == conversation_id)
            )
            await session.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
            await session.commit()

    async def add_message(self, conversation_id: str, data: MessageCreate) -> MessageResponse:
        async with self._session_factory() as session:
            # Verify conversation exists
            result = await session.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conv = result.scalar_one_or_none()
            if conv is None:
                raise NotFoundError(f"Conversation {conversation_id} not found.")

            msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                role=data.role,
                content=data.content,
                thinking_content=data.thinking_content,
                thinking_duration_ms=data.thinking_duration_ms,
            )
            session.add(msg)

            # Touch updated_at on the conversation
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(timezone.utc))
            )

            await session.commit()
            await session.refresh(msg)

            return _message_to_response(msg)

    async def export_conversation(self, conversation_id: str, format: str = "json") -> str | dict:
        """Export conversation as JSON dict or Markdown string."""
        conv_response = await self.get_conversation(conversation_id)

        if format == "markdown":
            lines = [f"# {conv_response.title}\n"]
            for msg in conv_response.messages:
                ts = datetime.fromtimestamp(msg.timestamp / 1000, tz=timezone.utc).isoformat()
                lines.append(f"### {msg.role.capitalize()} ({ts})\n")
                lines.append(f"{msg.content}\n")
                lines.append("---\n")
            return "\n".join(lines)

        return conv_response.model_dump()
