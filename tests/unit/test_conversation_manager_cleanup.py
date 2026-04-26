from unittest.mock import AsyncMock, patch

import pytest

from astrbot.core.conversation_mgr import ConversationManager


class FakeConversationDB:
    def __init__(self, *, deleted=True):
        self.deleted_conversations = []
        self.deleted_sessions = []
        self.deleted = deleted

    async def delete_conversation(self, cid):
        self.deleted_conversations.append(cid)
        return self.deleted

    async def delete_conversations_by_user_id(self, user_id):
        self.deleted_sessions.append(user_id)


@pytest.mark.asyncio
async def test_delete_conversation_triggers_registered_conversation_cleanup():
    manager = ConversationManager(FakeConversationDB())
    cleanup = AsyncMock()
    manager.register_on_conversation_deleted(cleanup)

    with patch("astrbot.core.conversation_mgr.sp.session_remove", new_callable=AsyncMock):
        await manager.delete_conversation("telegram:FriendMessage:user1", "conversation-1")

    cleanup.assert_awaited_once_with("conversation-1")


@pytest.mark.asyncio
async def test_delete_conversation_skips_cleanup_when_no_conversation_was_deleted():
    manager = ConversationManager(FakeConversationDB(deleted=False))
    cleanup = AsyncMock()
    manager.register_on_conversation_deleted(cleanup)

    await manager.delete_conversation("telegram:FriendMessage:user1", "conversation-1")

    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_conversation_clears_current_state_before_cleanup_callback():
    umo = "telegram:FriendMessage:user1"
    manager = ConversationManager(FakeConversationDB())
    manager.session_conversations[umo] = "conversation-1"
    observed_current = []

    async def cleanup(_conversation_id):
        observed_current.append(manager.session_conversations.get(umo))

    manager.register_on_conversation_deleted(cleanup)

    with patch("astrbot.core.conversation_mgr.sp.session_remove", new_callable=AsyncMock):
        await manager.delete_conversation(umo, "conversation-1")

    assert observed_current == [None]


@pytest.mark.asyncio
async def test_delete_conversation_isolates_callback_exceptions():
    manager = ConversationManager(FakeConversationDB())
    failing_cleanup = AsyncMock(side_effect=RuntimeError("boom"))
    successful_cleanup = AsyncMock()
    manager.register_on_conversation_deleted(failing_cleanup)
    manager.register_on_conversation_deleted(successful_cleanup)

    with patch("astrbot.core.conversation_mgr.sp.session_remove", new_callable=AsyncMock):
        await manager.delete_conversation("telegram:FriendMessage:user1", "conversation-1")

    failing_cleanup.assert_awaited_once_with("conversation-1")
    successful_cleanup.assert_awaited_once_with("conversation-1")


@pytest.mark.asyncio
async def test_delete_session_triggers_registered_session_cleanup():
    manager = ConversationManager(FakeConversationDB())
    cleanup = AsyncMock()
    manager.register_on_session_deleted(cleanup)

    with patch("astrbot.core.conversation_mgr.sp.session_remove", new_callable=AsyncMock):
        await manager.delete_conversations_by_user_id("telegram:FriendMessage:user1")

    cleanup.assert_awaited_once_with("telegram:FriendMessage:user1")
