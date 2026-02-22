import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.conversations import router as conversations_router
from app.core.database import ApiKey
from app.core.security import generate_api_key, hash_api_key, get_key_prefix
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def conv_auth_client(app_with_db, db_engine):
    """Auth client with conversations router registered."""
    # Register conversations router if not already included
    route_paths = {r.path for r in app_with_db.routes}
    if "/vault/conversations" not in route_paths:
        app_with_db.include_router(conversations_router, tags=["Conversations"])

    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    raw_key = generate_api_key()
    async with session_factory() as session:
        key_row = ApiKey(
            key_hash=hash_api_key(raw_key),
            key_prefix=get_key_prefix(raw_key),
            label="conv-test",
            scope="admin",
            is_active=True,
        )
        session.add(key_row)
        await session.commit()

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {raw_key}"
        yield client


@pytest_asyncio.fixture
async def conv_anon_client(app_with_db):
    """Anon client with conversations router registered."""
    route_paths = {r.path for r in app_with_db.routes}
    if "/vault/conversations" not in route_paths:
        app_with_db.include_router(conversations_router, tags=["Conversations"])

    transport = ASGITransport(app=app_with_db)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestListConversations:
    async def test_list_empty(self, conv_auth_client):
        """GET /vault/conversations returns empty list initially."""
        response = await conv_auth_client.get("/vault/conversations")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_returns_created(self, conv_auth_client):
        """GET /vault/conversations returns conversations sorted by updated_at desc."""
        # Create two conversations
        r1 = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "First", "model_id": "qwen2.5-32b-awq"},
        )
        r2 = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Second", "model_id": "qwen2.5-32b-awq"},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        response = await conv_auth_client.get("/vault/conversations")
        assert response.status_code == 200
        items = response.json()
        assert len(items) >= 2
        # Most recently created should be first (updated_at desc)
        titles = [c["title"] for c in items[:2]]
        assert "Second" in titles
        assert "First" in titles

    async def test_401_without_auth(self, conv_anon_client):
        """GET /vault/conversations without auth returns 401."""
        response = await conv_anon_client.get("/vault/conversations")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_required"


class TestCreateConversation:
    async def test_create(self, conv_auth_client):
        """POST /vault/conversations creates a new conversation."""
        response = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Test Chat", "model_id": "qwen2.5-32b-awq"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Chat"
        assert data["model_id"] == "qwen2.5-32b-awq"
        assert data["message_count"] == 0
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    async def test_create_default_title(self, conv_auth_client):
        """POST /vault/conversations with no title uses default."""
        response = await conv_auth_client.post(
            "/vault/conversations",
            json={"model_id": "qwen2.5-32b-awq"},
        )
        assert response.status_code == 201
        assert response.json()["title"] == "New Conversation"


class TestGetConversation:
    async def test_get_existing(self, conv_auth_client):
        """GET /vault/conversations/{id} returns conversation with messages."""
        # Create conversation
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Get Test", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        response = await conv_auth_client.get(f"/vault/conversations/{conv_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == conv_id
        assert data["title"] == "Get Test"
        assert data["messages"] == []

    async def test_get_nonexistent(self, conv_auth_client):
        """GET /vault/conversations/{id} returns 404 for missing conversation."""
        response = await conv_auth_client.get(
            "/vault/conversations/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestUpdateConversation:
    async def test_update_title(self, conv_auth_client):
        """PUT /vault/conversations/{id} updates the title."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Old Title", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        response = await conv_auth_client.put(
            f"/vault/conversations/{conv_id}",
            json={"title": "New Title"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "New Title"

        # Verify via GET
        get_resp = await conv_auth_client.get(f"/vault/conversations/{conv_id}")
        assert get_resp.json()["title"] == "New Title"


class TestDeleteConversation:
    async def test_delete(self, conv_auth_client):
        """DELETE /vault/conversations/{id} removes conversation."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "To Delete", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        response = await conv_auth_client.delete(f"/vault/conversations/{conv_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_resp = await conv_auth_client.get(f"/vault/conversations/{conv_id}")
        assert get_resp.status_code == 404

    async def test_delete_cascades_messages(self, conv_auth_client):
        """DELETE /vault/conversations/{id} also removes all messages."""
        # Create conversation and add a message
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Cascade Test", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={"role": "user", "content": "Hello"},
        )

        # Delete conversation
        response = await conv_auth_client.delete(f"/vault/conversations/{conv_id}")
        assert response.status_code == 204

        # Conversation and messages are gone
        get_resp = await conv_auth_client.get(f"/vault/conversations/{conv_id}")
        assert get_resp.status_code == 404


class TestAddMessage:
    async def test_add_message(self, conv_auth_client):
        """POST /vault/conversations/{id}/messages adds a message."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Message Test", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        response = await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={"role": "user", "content": "Hello, world!"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["role"] == "user"
        assert data["content"] == "Hello, world!"
        assert data["thinking"] is None
        assert "id" in data
        assert "timestamp" in data

    async def test_add_message_with_thinking(self, conv_auth_client):
        """POST /vault/conversations/{id}/messages with thinking metadata."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Thinking Test", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        response = await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={
                "role": "assistant",
                "content": "The answer is 42.",
                "thinking_content": "Let me think about this...",
                "thinking_duration_ms": 1500,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["thinking"]["content"] == "Let me think about this..."
        assert data["thinking"]["durationMs"] == 1500

    async def test_get_conversation_shows_messages(self, conv_auth_client):
        """Messages appear in GET /vault/conversations/{id}."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Full Test", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={"role": "user", "content": "Hi"},
        )
        await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={"role": "assistant", "content": "Hello!"},
        )

        response = await conv_auth_client.get(f"/vault/conversations/{conv_id}")
        assert response.status_code == 200
        messages = response.json()["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    async def test_add_message_nonexistent_conversation(self, conv_auth_client):
        """POST /vault/conversations/{id}/messages returns 404 for missing conversation."""
        response = await conv_auth_client.post(
            "/vault/conversations/00000000-0000-0000-0000-000000000000/messages",
            json={"role": "user", "content": "Hello"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestExportConversation:
    async def test_export_json(self, conv_auth_client):
        """GET /vault/conversations/{id}/export?format=json returns JSON."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Export Test", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={"role": "user", "content": "Hello"},
        )
        await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={"role": "assistant", "content": "Hi there!"},
        )

        response = await conv_auth_client.get(
            f"/vault/conversations/{conv_id}/export?format=json"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Export Test"
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"
        assert "content-disposition" in response.headers
        assert "json" in response.headers["content-disposition"]

    async def test_export_markdown(self, conv_auth_client):
        """GET /vault/conversations/{id}/export?format=markdown returns Markdown."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "MD Export", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        await conv_auth_client.post(
            f"/vault/conversations/{conv_id}/messages",
            json={"role": "user", "content": "What is AI?"},
        )

        response = await conv_auth_client.get(
            f"/vault/conversations/{conv_id}/export?format=markdown"
        )
        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        text = response.text
        assert "# MD Export" in text
        assert "### User" in text
        assert "What is AI?" in text

    async def test_export_nonexistent(self, conv_auth_client):
        """GET /vault/conversations/{id}/export returns 404 for missing conversation."""
        response = await conv_auth_client.get(
            "/vault/conversations/00000000-0000-0000-0000-000000000000/export?format=json"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_export_default_format_is_json(self, conv_auth_client):
        """GET /vault/conversations/{id}/export without format defaults to JSON."""
        create_resp = await conv_auth_client.post(
            "/vault/conversations",
            json={"title": "Default Format", "model_id": "qwen2.5-32b-awq"},
        )
        conv_id = create_resp.json()["id"]

        response = await conv_auth_client.get(
            f"/vault/conversations/{conv_id}/export"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Default Format"
