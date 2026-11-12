import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import openpyxl
import pytest
from pptx import Presentation
from pypdf import PdfReader

import db
from agent.loop import run_agent_turn
from agent.tools import CORE_TOOL_NAMES, route_tools, execute_tool
from agent.verifier import verify_and_ground_reply
from connectors.guardrails import check_fake_success_guard
from core.file_dispatch import get_artifact
from file_generator import build_document_artifact, build_styled_pdf
from llm.provider import get_fake_llm, reset_fake_llm


def test_build_styled_pdf_renders_text_markdown_and_xml_special_chars():
    pdf_bytes = build_styled_pdf(
        "Contacts & Summary <Q4>",
        [{"heading": "Team", "text": "## Engineering\n- Kunal Joshi <kunal@test.com>\n- Rohan & Co."}],
    )
    assert pdf_bytes.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Contacts & Summary" in extracted
    assert "Kunal Joshi" in extracted
    assert "Rohan & Co." in extracted


@pytest.mark.parametrize("fmt", ["pdf", "xlsx", "csv", "docx", "pptx", "md", "txt", "json", "html", "ics"])
def test_build_document_artifact_supports_all_common_formats(fmt):
    data_bytes, fname, mime = build_document_artifact(
        fmt=fmt,
        title="Udaipur Trip Plan",
        content="Day 1: Arrival & Pichola Lake",
        sections=[
            {
                "heading": "Itinerary",
                "paragraphs": ["Leave Jodhpur by 7:00 AM."],
                "bullets": ["Haldighati stop", "City Palace visit"],
                "table": [["Item", "Cost"], ["Fuel", "1500"], ["Hotel", "3500"]],
            }
        ],
        slides=[{"title": "Route Overview", "bullets": ["250 km via Pali", "5 hours drive"]}],
        headers=["Item", "Cost"],
        rows=[["Fuel", 1500], ["Hotel", 3500]],
        data={"trip": "Udaipur", "distance_km": 250},
    )
    assert len(data_bytes) > 20
    assert fname.endswith(f".{fmt}")
    assert mime

    if fmt == "pdf":
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data_bytes)).pages)
        assert "Udaipur Trip Plan" in text
        assert "City Palace" in text
    elif fmt == "xlsx":
        wb = openpyxl.load_workbook(io.BytesIO(data_bytes))
        ws = wb.active
        assert ws["A1"].value == "Item"
        assert ws["B2"].value == 1500
    elif fmt == "csv":
        decoded = data_bytes.decode("utf-8-sig")
        assert "Item,Cost" in decoded
        assert "Fuel,1500" in decoded
    elif fmt == "docx":
        with zipfile.ZipFile(io.BytesIO(data_bytes)) as zf:
            assert "word/document.xml" in zf.namelist()
            xml_txt = zf.read("word/document.xml").decode("utf-8")
            assert "Udaipur Trip Plan" in xml_txt
            assert "City Palace visit" in xml_txt
    elif fmt == "pptx":
        prs = Presentation(io.BytesIO(data_bytes))
        assert len(prs.slides) >= 2
    elif fmt == "json":
        parsed = json.loads(data_bytes.decode("utf-8"))
        assert parsed["trip"] == "Udaipur"


@pytest.mark.asyncio
async def test_generate_excel_and_pdf_actually_dispatch_files_on_telegram():
    chat_id = 998877830
    db.ensure_user(chat_id)
    db.save_contact_memory(chat_id, "Kunal Joshi", role_title="CTO", email="kunal@tech.in", phone="9112233445")

    sent_calls = []

    async def fake_send_file(chat_id, filename, content_bytes, caption="", mime_type="application/octet-stream"):
        sent_calls.append((chat_id, filename, content_bytes, caption, mime_type))
        return {"ok": True}

    with patch("main.send_file", side_effect=fake_send_file):
        # 1. Excel contacts export
        res_xl = await execute_tool(chat_id, "generate_excel", {"data_type": "contacts"})
        assert res_xl["status"] == "success"
        assert res_xl["delivered"] is True
        assert res_xl["files_sent"] == ["contacts.xlsx"]
        wb = openpyxl.load_workbook(io.BytesIO(sent_calls[0][2]))
        values = [cell.value for row in wb.active.iter_rows() for cell in row]
        assert "Kunal Joshi" in values

        # 2. PDF contacts export
        res_pdf = await execute_tool(chat_id, "generate_pdf", {"title": "Contacts List", "data_type": "contacts"})
        assert res_pdf["status"] == "success"
        assert res_pdf["delivered"] is True
        assert res_pdf["filename"].endswith(".pdf")
        pdf_txt = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(sent_calls[1][2])).pages)
        assert "Kunal Joshi" in pdf_txt

        # 3. Generic custom Word & PowerPoint via generate_file
        res_docx = await execute_tool(
            chat_id,
            "generate_file",
            {"format": "docx", "title": "Project Brief", "content": "Architecture notes\n- Zero hardcoding"},
        )
        assert res_docx["status"] == "success"
        assert res_docx["filename"] == "Project_Brief.docx"
        assert res_docx["delivered"] is True

        res_pptx = await execute_tool(
            chat_id,
            "generate_file",
            {"format": "pptx", "title": "Q4 Pitch", "slides": [{"title": "Traction", "bullets": ["10k users"]}]},
        )
        assert res_pptx["status"] == "success"
        assert res_pptx["filename"] == "Q4_Pitch.pptx"
        assert res_pptx["delivered"] is True

    assert len(sent_calls) == 4


@pytest.mark.asyncio
async def test_failed_telegram_delivery_never_fakes_success_in_guardrails():
    chat_id = 998877831
    db.ensure_user(chat_id)

    with patch("main.send_file", side_effect=RuntimeError("Telegram sendDocument failed (400): chat not found")):
        res = await execute_tool(chat_id, "generate_excel", {"data_type": "contacts"})

    assert res["status"] == "error"
    assert res["error_type"] == "delivery_failed"
    assert res["delivered"] is False

    ok_guard, err_msg = check_fake_success_guard(
        "I have sent the file contacts.xlsx over to you!",
        [res],
        user_query="send my contacts excel",
    )
    assert ok_guard is False
    assert err_msg

    # Verify Verifier also rejects undelivered file claims when LLM claim-checker flags action_done
    with patch("agent.verifier.audit_claims_with_llm", new=AsyncMock(return_value={
        "claims_action_done": True,
        "which_action": "generate_excel",
        "claims_external_fact": False,
        "sourced_by_tool": False,
    })), patch("agent.verifier.rewrite_reply_with_grounding", new=AsyncMock(return_value="I generated contacts.xlsx, but delivery failed right now.")):
        grounded, final_msg, violations = await verify_and_ground_reply(
            draft_reply="I have generated your contacts Excel file (contacts.xlsx) and sent it over!",
            tool_results=[res],
            user_query="my contact list and generate an excel file",
            user_lang="english",
        )
        assert grounded is False
        assert len(violations) > 0
        assert "delivery failed" in final_msg.lower()


@pytest.mark.asyncio
async def test_app_channel_returns_downloadable_attachments_with_ownership_isolation(monkeypatch):
    monkeypatch.setenv("USE_FAKE_LLM", "1")
    reset_fake_llm()
    fake_llm = get_fake_llm()

    chat_id = 998877832
    other_chat_id = 998877833
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, reply_language="english", tone="concise")

    fake_llm.queue_poisoned_tool_call(
        "generate_file",
        {"format": "csv", "title": "Expenses", "headers": ["Category", "Amount"], "rows": [["Travel", 1200]]},
    )
    fake_llm.queue_response(text="I have created your Expenses CSV file (Expenses.csv) and attached it for download.")

    res = await run_agent_turn(chat_id, "create a csv file of my travel expenses", channel="app")
    assert "generate_file" in res["tool_calls_executed"]
    assert len(res["attachments"]) == 1
    att = res["attachments"][0]
    assert att["filename"] == "Expenses.csv"
    assert att["download_url"].startswith("/api/v1/files/")

    # Owner can fetch artifact bytes; other user cannot
    found = get_artifact(att["artifact_id"], chat_id)
    assert found is not None
    assert "Travel,1200" in found[2].decode("utf-8-sig")
    assert get_artifact(att["artifact_id"], other_chat_id) is None
    reset_fake_llm()


def test_route_tools_exposes_universal_file_formats():
    routed_docx = {t["function"]["name"] for t in route_tools("create a word docx report of my meetings")}
    assert "generate_file" in routed_docx
    routed_pptx = {t["function"]["name"] for t in route_tools("make a powerpoint pptx slides deck")}
    assert "generate_file" in routed_pptx
    assert "execute_python_code" in CORE_TOOL_NAMES
