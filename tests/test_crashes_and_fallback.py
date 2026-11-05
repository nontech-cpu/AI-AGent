import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from agent.tools import execute_tool
import main
import db

@pytest.mark.asyncio
async def test_set_user_timezone_no_unbound_local():
    """Verify that execute_tool('set_user_timezone') does not crash with UnboundLocalError."""
    chat_id = 998877221
    res = await execute_tool(chat_id, "set_user_timezone", {"timezone": "Asia/Kolkata"})
    assert res["status"] == "success"
    assert res["timezone"] == "Asia/Kolkata"

@pytest.mark.asyncio
async def test_agent_loop_ist_scoping_no_unbound_local():
    """Verify that agent/loop.py handles email confirmation payload creation without UnboundLocalError: 'IST'."""
    from agent.loop import run_agent_turn
    chat_id = 998877222
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_persona(chat_id, "job")
    db.set_state(chat_id, "active")

    mock_llm_resp = MagicMock()
    mock_llm_resp.status_code = 200
    mock_llm_resp.json.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "send_email",
                        "arguments": '{"to": "colleague@example.com", "subject": "Update", "body": "Hello"}'
                    }
                }]
            }
        }],
        "usage": {"total_tokens": 50}
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_llm_resp), \
         patch("agent.loop.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {
            "status": "needs_confirmation",
            "action": "send_email",
            "to": "colleague@example.com",
            "subject": "Update",
            "body": "Hello"
        }
        turn_res = await run_agent_turn(chat_id, "Send an email to colleague@example.com saying Hello")
        assert "Please confirm" in turn_res["reply"]
        assert "colleague@example.com" in turn_res["reply"]

@pytest.mark.asyncio
async def test_unhandled_crash_triggers_network_fluctuation_fallback():
    """
    Proves that an unhandled crash in run_agent_turn triggers the Telegram error handler in main.py,
    which sends: 'Haanji, network mein thoda fluctuation ho gaya tha. Ek baar dobara bataenge?'
    """
    chat_id = 998877223
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_persona(chat_id, "job")
    db.set_state(chat_id, "active")

    with patch("agent.loop.run_agent_turn", side_effect=UnboundLocalError("cannot access local variable 'IST'")), \
         patch("main.say", new_callable=AsyncMock) as mock_say:
        
        # Invoke handle_user_text in v2 mode
        with patch.dict("os.environ", {"AGENT_CORE": "v2"}):
            await main.handle_user_text(chat_id, "Please schedule this for me")
        
        # Verify that mock_say was called with the exact network fluctuation fallback message
        mock_say.assert_called_once()
        sent_reply = mock_say.call_args[0][1]
        assert "network mein thoda fluctuation ho gaya tha" in sent_reply
        assert sent_reply == "Haanji, network mein thoda fluctuation ho gaya tha. Ek baar dobara bataenge?"
