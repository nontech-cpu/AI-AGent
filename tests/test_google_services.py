"""
Tests for Google Services integration (Calendar, Gmail, Drive).
Uses mock HTTP responses to test edge cases, error handling, and authorization.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import google_services


@pytest.mark.asyncio
async def test_access_token_for_chat_not_connected():
    with patch("google_services.engine.connect") as mock_conn:
        mock_conn.return_value.__enter__.return_value.execute.return_value.first.return_value = (1,)
        with patch("google_services.get_google_token", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                await google_services.access_token_for_chat(12345)
            assert "not connected" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_create_meeting_success():
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@example.com")
        
        mock_response = {
            "id": "cal_evt_12345",
            "htmlLink": "https://calendar.google.com/event?eid=mock123",
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}
                ]
            }
        }
        
        with patch("google_services._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = mock_response
            res = await google_services.create_meeting(
                chat_id=12345,
                title="Client Discussion",
                start_iso="2026-10-21T15:00:00",
                duration_minutes=45,
                attendees=["client@example.com"]
            )
            
            assert res["id"] == "cal_evt_12345"
            assert res["meet_url"] == "https://meet.google.com/abc-defg-hij"
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][0] == "POST"
            assert "calendar" in call_args[0][1]
            assert call_args[1]["json"]["summary"] == "Client Discussion"


@pytest.mark.asyncio
async def test_send_email_success():
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@example.com")
        
        with patch("google_services._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "msg_gmail_999"}
            msg_id = await google_services.send_email(
                chat_id=12345,
                to="partner@example.com",
                subject="Status Report",
                body="Everything on track."
            )
            
            assert msg_id == "msg_gmail_999"
            mock_req.assert_called_once()
            call_args = mock_req.call_args
            assert call_args[0][0] == "POST"
            assert "messages/send" in call_args[0][1]
            assert "raw" in call_args[1]["json"]


@pytest.mark.asyncio
async def test_upload_text_file_drive():
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@example.com")
        
        mock_client = AsyncMock()
        mock_post_res = MagicMock()
        mock_post_res.is_error = False
        mock_post_res.json.return_value = {
            "id": "drive_file_789",
            "name": "notes.txt",
            "webViewLink": "https://drive.google.com/file/d/drive_file_789/view"
        }
        mock_client.post.return_value = mock_post_res
        
        with patch("google_services.httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value = mock_client
            res = await google_services.upload_text_file(
                chat_id=12345,
                filename="notes.txt",
                content="Meeting notes contents"
            )
            
            assert res["id"] == "drive_file_789"
            assert "webViewLink" in res
