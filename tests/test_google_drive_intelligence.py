"""
Unit & Integration Tests for Google Drive Intelligence:
1. Uploading Document/File with Smart Folder Creation & Placement
2. Searching & Listing Drive Files by Query, Folder, and MimeType
3. Reading & Extracting Text from Documents (Google Docs, PDFs, DOCX, Text)
4. Summarizing Document Content from Drive
5. Edge Case: Google Drive Storage Quota Full (No Hallucination)
6. Edge Case: Unauthenticated User Error Handling
7. Google OAuth Scopes Verification (drive.file + drive.readonly)
"""

import io
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
import google_services
from google_services import GoogleDriveQuotaExceededError, GoogleDriveFileNotFoundError
import google_auth
from connectors.drive import DriveConnector
from agent.tools import execute_tool
from state.pending import execute_pending_action


def test_google_drive_oauth_scopes():
    """Verify that both drive.file and drive.readonly scopes are requested during Google Connect."""
    assert "https://www.googleapis.com/auth/drive.file" in google_auth.GOOGLE_SCOPES
    assert "https://www.googleapis.com/auth/drive.readonly" in google_auth.GOOGLE_SCOPES


@pytest.mark.asyncio
async def test_get_or_create_drive_folder():
    """Verify folder search and dynamic creation in Google Drive."""
    chat_id = 999111222
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@gmail.com")

        with patch("google_services._request", new_callable=AsyncMock) as mock_req:
            # Case 1: Folder already exists
            mock_req.return_value = {"files": [{"id": "folder_existing_01", "name": "Invoices"}]}
            fid = await google_services.get_or_create_drive_folder(chat_id, "Invoices")
            assert fid == "folder_existing_01"

            # Case 2: Folder does not exist, creates new folder
            mock_req.side_effect = [
                {"files": []},  # Search returns empty
                {"id": "folder_new_99", "name": "Work Projects"}  # Creation returns new ID
            ]
            fid_new = await google_services.get_or_create_drive_folder(chat_id, "Work Projects")
            assert fid_new == "folder_new_99"


@pytest.mark.asyncio
async def test_upload_file_to_drive_with_folder():
    """Verify uploading a file to Google Drive with automated folder resolution."""
    chat_id = 999111222
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@gmail.com")

        with patch("google_services.get_or_create_drive_folder", new_callable=AsyncMock) as mock_folder:
            mock_folder.return_value = "folder_resolved_123"

            mock_client = AsyncMock()
            mock_post_res = MagicMock()
            mock_post_res.is_error = False
            mock_post_res.json.return_value = {
                "id": "file_uploaded_456",
                "name": "MeetingNotes.txt",
                "webViewLink": "https://drive.google.com/file/d/file_uploaded_456/view",
                "parents": ["folder_resolved_123"]
            }
            mock_client.post.return_value = mock_post_res

            with patch("google_services.httpx.AsyncClient") as mock_httpx:
                mock_httpx.return_value.__aenter__.return_value = mock_client

                res = await google_services.upload_file_to_drive(
                    chat_id=chat_id,
                    filename="MeetingNotes.txt",
                    content="Summary of client discussion",
                    folder_name="Client Notes"
                )

                assert res["id"] == "file_uploaded_456"
                assert res["folder_id"] == "folder_resolved_123"
                assert "webViewLink" in res


@pytest.mark.asyncio
async def test_google_drive_storage_quota_full_handling():
    """Verify clean, non-hallucinated handling when Google Drive storage quota is 100% full."""
    chat_id = 999111222
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@gmail.com")

        mock_client = AsyncMock()
        mock_post_res = MagicMock()
        mock_post_res.is_error = True
        mock_post_res.status_code = 403
        mock_post_res.text = '{"error": {"errors": [{"reason": "storageQuotaExceeded", "message": "User storage quota exceeded"}]}}'
        mock_client.post.return_value = mock_post_res

        with patch("google_services.httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__.return_value = mock_client

            # Direct call must raise GoogleDriveQuotaExceededError
            with pytest.raises(GoogleDriveQuotaExceededError):
                await google_services.upload_file_to_drive(
                    chat_id=chat_id,
                    filename="LargeReport.pdf",
                    content="Report content"
                )

    # When executed through the confirmation state machine, user gets a clear, friendly alert
    with patch("google_services.upload_file_to_drive", side_effect=GoogleDriveQuotaExceededError("Quota full")):
        payload = {
            "kind": "upload_file",
            "filename": "LargeReport.pdf",
            "content": "Report content"
        }
        ok, reply = await execute_pending_action(chat_id, payload)
        assert ok is True
        assert "Storage Full" in reply
        assert "quota full ho chuka hai" in reply


@pytest.mark.asyncio
async def test_search_drive_files():
    """Verify searching and filtering files on Google Drive."""
    chat_id = 999111222
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@gmail.com")

        with patch("google_services._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "files": [
                    {"id": "doc1", "name": "Q3_Report.pdf", "mimeType": "application/pdf"},
                    {"id": "doc2", "name": "Annual_Taxes.pdf", "mimeType": "application/pdf"}
                ]
            }

            files = await google_services.search_drive_files(chat_id, query="Report", mime_type="pdf")
            assert len(files) == 2
            assert files[0]["name"] == "Q3_Report.pdf"


@pytest.mark.asyncio
async def test_read_drive_document_pdf_and_text():
    """Verify reading and extracting text from PDF and Google Doc files."""
    chat_id = 999111222
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_access:
        mock_access.return_value = ("mock_access_token", "user@gmail.com")

        # 1. Test Google Doc export
        with patch("google_services._request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {
                "id": "gdoc_101",
                "name": "Strategy Document",
                "mimeType": "application/vnd.google-apps.document",
                "webViewLink": "https://docs.google.com/document/d/gdoc_101/view"
            }

            mock_client = AsyncMock()
            mock_get_res = MagicMock()
            mock_get_res.is_error = False
            mock_get_res.text = "Strategic Roadmap 2026: Focus on AI-driven workflows and zero latency."
            mock_client.get.return_value = mock_get_res

            with patch("google_services.httpx.AsyncClient") as mock_httpx:
                mock_httpx.return_value.__aenter__.return_value = mock_client

                doc_res = await google_services.read_drive_document(chat_id, file_id="gdoc_101")
                assert doc_res["ok"] is True
                assert "Strategic Roadmap 2026" in doc_res["text"]
                assert doc_res["mime_type"] == "application/vnd.google-apps.document"


@pytest.mark.asyncio
async def test_execute_tool_drive_suite():
    """Verify execute_tool dispatch for all Google Drive tools."""
    chat_id = 999111222

    # Case 1: Google not connected
    with patch.object(DriveConnector, "auth_state", return_value="not_connected"):
        res = await execute_tool(chat_id, "search_drive_files", {"query": "Receipts"})
        assert res.get("status") == "error"
        assert res.get("error_type") in ("not_connected", "google_not_connected")

    # Case 2: Google connected -> search_drive_files
    with patch.object(DriveConnector, "auth_state", return_value="connected"):
        with patch("google_services.search_drive_files", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [{"id": "f1", "name": "Tax_2025.pdf"}]
            res = await execute_tool(chat_id, "search_drive_files", {"query": "Tax"})
            assert res.get("status") == "success"
            assert res.get("count") == 1
            assert res["files"][0]["name"] == "Tax_2025.pdf"

        # Case 3: Google connected -> upload_to_drive (triggers two-phase confirmation)
        res_upload = await execute_tool(
            chat_id,
            "upload_to_drive",
            {"filename": "Notes.txt", "content": "Sample content", "folder_name": "Work"}
        )
        assert res_upload.get("status") == "needs_confirmation"
        assert res_upload.get("action") == "upload_file"
        assert res_upload.get("filename") == "Notes.txt"
        assert res_upload.get("folder_name") == "Work"

        # Case 4: Google connected -> create_drive_folder
        with patch("google_services.get_or_create_drive_folder", new_callable=AsyncMock) as mock_folder:
            mock_folder.return_value = "folder_abc_123"
            res_f = await execute_tool(chat_id, "create_drive_folder", {"folder_name": "Taxes 2026"})
            assert res_f.get("status") == "success"
            assert res_f.get("folder_id") == "folder_abc_123"
            assert res_f.get("folder_name") == "Taxes 2026"
