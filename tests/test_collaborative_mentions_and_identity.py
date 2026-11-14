import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import db
from core.avatar_generator import generate_avatar_svg
from core.mentions_engine import (
    RecipientProfile,
    TargetedDirective,
    deconstruct_targeted_directives,
    execute_targeted_mentions_dispatch,
    extract_mentions,
    resolve_recipient_identity,
)
from main import app

client = TestClient(app)


# =============================================================================
# 1. Pillar 1 Tests: Unique Usernames & Reddit-Style Avatars
# =============================================================================

def test_pillar1_username_validation_and_case_insensitive_uniqueness():
    chat_a = 99881101
    chat_b = 99881102
    db.ensure_user(chat_a)
    db.ensure_user(chat_b)

    # 1. Validation rules: Length & Characters
    ok, msg = db.check_username_available("ab")
    assert ok is False
    assert "between 3 and 30" in msg

    ok, msg = db.check_username_available("invalid user!")
    assert ok is False
    assert "letters, numbers, and underscores" in msg

    # 2. Reserved usernames
    for res_name in ["admin", "support", "peculiar", "bot", "official"]:
        ok, msg = db.check_username_available(res_name)
        assert ok is False
        assert "reserved" in msg

    # 3. User A claims 'keshav_dev'
    claimed_a, msg_a = db.claim_username(chat_a, "keshav_dev")
    assert claimed_a is True
    assert "@keshav_dev" in msg_a

    # 4. User B attempts to claim same username (case-insensitive clash: KESHAV_DEV)
    claimed_b, msg_b = db.claim_username(chat_b, "KESHAV_DEV")
    assert claimed_b is False
    assert "already taken" in msg_b.lower()

    # User B chooses an available username
    claimed_b2, msg_b2 = db.claim_username(chat_b, "rahul_lead")
    assert claimed_b2 is True


def test_pillar1_reddit_style_avatar_customization():
    chat_id = 99881103
    db.ensure_user(chat_id)

    custom_cfg = {
        "primary_color": "#EC4899",  # Pink
        "bg_color": "#FCE7F3",
        "eye_style": "wink",
        "antenna_style": "star"
    }

    # Save configuration
    saved = db.update_avatar_config(chat_id, custom_cfg)
    assert saved["primary_color"] == "#EC4899"

    # Retrieve via profile
    prof = db.get_user_profile(chat_id)
    assert prof["avatar_config"]["primary_color"] == "#EC4899"
    assert prof["avatar_config"]["antenna_style"] == "star"

    # Vector SVG generation
    svg = generate_avatar_svg("keshav_dev", prof["avatar_config"])
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "#EC4899" in svg
    assert "#FCE7F3" in svg


# =============================================================================
# 2. Pillar 2 Tests: Cross-Channel Identity & Presence Mesh
# =============================================================================

def test_pillar2_presence_mesh_resolves_app_user_and_contact_memory():
    sender_chat = 99881104
    db.ensure_user(sender_chat)

    # Setup Registered App User: 'keshav_app'
    app_user_chat = 99881105
    db.ensure_user(app_user_chat)
    db.claim_username(app_user_chat, "keshav_app")

    # Setup Personal Contact Memory for Sender: 'Devender' with linked phone
    db.save_contact_memory(
        sender_chat,
        name="Devender Sharma",
        phone="+919876543210",
        role_title="Backend Lead"
    )

    # 1. Resolve registered app user by @handle
    recip_keshav = resolve_recipient_identity(sender_chat, "keshav_app")
    assert recip_keshav.handle == "keshav_app"
    assert recip_keshav.is_registered_app_user is True
    assert recip_keshav.target_channel == "app"

    # 2. Resolve personal contact by @handle with _contact suffix
    recip_devender = resolve_recipient_identity(sender_chat, "devender_contact")
    assert recip_devender.display_name == "Devender Sharma"
    assert recip_devender.phone_number == "+919876543210"

    # 3. Resolve unlinked handle
    recip_unknown = resolve_recipient_identity(sender_chat, "random_person")
    assert recip_unknown.target_channel == "unlinked"


# =============================================================================
# 3. Pillar 3 Tests: Mention Lexer & Targeted Scope Decompiler (Zero-Leakage Guard)
# =============================================================================

def test_pillar3_extract_mentions_tokens():
    text_sample = "Hey @devender_contact please check this with @rahul_lead and cc @keshav"
    mentions = extract_mentions(text_sample)
    assert mentions == ["devender_contact", "rahul_lead", "keshav"]


def test_pillar3_strict_privacy_scope_partitioning_zero_leakage():
    """
    Critical Security Test:
    Devender's private message MUST NOT leak Rahul's name or task.
    Rahul's private message MUST NOT leak Devender's name or task.
    """
    raw_prompt = "@devender_contact u need to work on project a and @rahul_contact u need to work on project b"

    recip_dev = RecipientProfile(handle="devender_contact", display_name="Devender", target_channel="telegram", telegram_chat_id=1111)
    recip_rah = RecipientProfile(handle="rahul_contact", display_name="Rahul", target_channel="app", user_id=2222)

    directives = deconstruct_targeted_directives(raw_prompt, [recip_dev, recip_rah])
    assert len(directives) == 2

    dev_directive = directives[0]
    rah_directive = directives[1]

    # Verify Devender's scoped directive
    assert dev_directive.recipient.handle == "devender_contact"
    assert "work on project a" in dev_directive.private_message.lower()
    # ZERO LEAKAGE ASSERTIONS FOR DEVENDER:
    assert "rahul" not in dev_directive.private_message.lower()
    assert "project b" not in dev_directive.private_message.lower()

    # Verify Rahul's scoped directive
    assert rah_directive.recipient.handle == "rahul_contact"
    assert "work on project b" in rah_directive.private_message.lower()
    # ZERO LEAKAGE ASSERTIONS FOR RAHUL:
    assert "devender" not in rah_directive.private_message.lower()
    assert "project a" not in rah_directive.private_message.lower()


def test_pillar3_single_mention_scoped_directive():
    raw_prompt = "@keshav please submit the financial budget report before 5 PM"
    recip_keshav = RecipientProfile(handle="keshav", display_name="Keshav", target_channel="app", user_id=3333)

    directives = deconstruct_targeted_directives(raw_prompt, [recip_keshav])
    assert len(directives) == 1
    assert "submit the financial budget report before 5 pm" in directives[0].private_message.lower()


# =============================================================================
# 4. Pillar 4 Tests: Omni-Channel Dispatch Engine & In-App Notifications
# =============================================================================

@pytest.mark.asyncio
async def test_pillar4_end_to_end_directed_dispatch_across_telegram_and_app():
    sender_chat = 99881106
    db.ensure_user(sender_chat)
    db.claim_username(sender_chat, "sender_boss")

    # Recipient 1: Telegram user
    tg_chat_id = 99881107
    db.ensure_user(tg_chat_id)
    db.claim_username(tg_chat_id, "tg_member")

    # Recipient 2: App user
    app_chat_id = 99881108
    db.ensure_user(app_chat_id)
    db.claim_username(app_chat_id, "app_member")
    app_uid = db.resolve_user_id(app_chat_id)

    prompt = "@tg_member_contact please review database migration and @app_member prepare the release notes"

    sent_tg_messages = []
    async def mock_tg_send(cid, msg):
        sent_tg_messages.append((cid, msg))

    with patch("main.send_telegram_text", side_effect=mock_tg_send):
        res = await execute_targeted_mentions_dispatch(sender_chat, prompt)

    assert res.status == "success"
    assert len(res.directives) == 2

    # 1. Telegram delivery verification
    assert len(sent_tg_messages) == 1
    tg_dest, tg_body = sent_tg_messages[0]
    assert tg_dest == tg_chat_id
    assert "review database migration" in tg_body
    assert "release notes" not in tg_body  # Zero leakage!

    # 2. In-App notification delivery verification
    inbox = db.get_in_app_notifications(app_uid)
    assert len(inbox) >= 1
    app_notif = inbox[0]
    assert "release notes" in app_notif["body"]
    assert "database migration" not in app_notif["body"]  # Zero leakage!


# =============================================================================
# 5. REST API Route Tests (api_v1.py)
# =============================================================================

def test_api_username_check_and_claim():
    cid = 99881109
    db.ensure_user(cid)

    # Check available
    resp = client.get("/api/v1/user/check-username?username=my_new_handle", headers={"X-Chat-Id": str(cid)})
    assert resp.status_code == 200
    assert resp.json()["available"] is True

    # Claim username
    resp_claim = client.post("/api/v1/user/claim-username", json={"username": "my_new_handle"}, headers={"X-Chat-Id": str(cid)})
    assert resp_claim.status_code == 200
    assert resp_claim.json()["username"] == "my_new_handle"

    # Profile check
    resp_prof = client.get("/api/v1/user/profile", headers={"X-Chat-Id": str(cid)})
    assert resp_prof.status_code == 200
    assert resp_prof.json()["username"] == "my_new_handle"

    # SVG Avatar endpoint
    resp_svg = client.get("/api/v1/user/my_new_handle/avatar.svg")
    assert resp_svg.status_code == 200
    assert "image/svg+xml" in resp_svg.headers.get("content-type", "")
    assert "<svg" in resp_svg.text


def test_mention_by_phone_number_resolves_contact_and_dispatches():
    sender_chat = 99881110
    db.ensure_user(sender_chat)

    # Devender is registered in users with phone number and telegram chat ID
    devender_tg_chat = 99881111
    db.ensure_user(devender_tg_chat)
    with db.engine.begin() as conn:
        conn.execute(
            db.text("UPDATE users SET phone_number = '+919876543210', first_name = 'Devender' WHERE telegram_chat_id = :c"),
            {"c": devender_tg_chat}
        )

    # Sender also has Devender in contact memories
    db.save_contact_memory(
        chat_id=sender_chat,
        name="Devender Sharma",
        phone="+919876543210"
    )

    # 1. Test resolving by @9876543210
    prof = resolve_recipient_identity(sender_chat, "9876543210")
    assert prof.target_channel == "telegram"
    assert prof.telegram_chat_id == devender_tg_chat
    assert "Devender" in prof.display_name

    # 2. Test resolving by @+919876543210
    prof_plus = resolve_recipient_identity(sender_chat, "+919876543210")
    assert prof_plus.target_channel == "telegram"
    assert prof_plus.telegram_chat_id == devender_tg_chat

    # 3. Test token extraction with phone numbers
    mentions = extract_mentions("@+919876543210 we have a meeting and @keshav work on ui")
    assert "+919876543210" in mentions
    assert "keshav" in mentions


@pytest.mark.asyncio
async def test_end_to_end_dispatch_with_phone_number_and_username():
    db._init_mentions_and_dispatches_tables()
    sender_chat = 99881112
    db.ensure_user(sender_chat)

    # Devender on Telegram with phone number
    dev_chat = 99881113
    db.ensure_user(dev_chat)
    with db.engine.begin() as conn:
        conn.execute(
            db.text("UPDATE users SET phone_number = '+919999988888', first_name = 'Devender' WHERE telegram_chat_id = :c"),
            {"c": dev_chat}
        )

    # Keshav on App
    kesh_chat = 99881114
    db.ensure_user(kesh_chat)
    db.claim_username(kesh_chat, "keshav_phone_test")
    kesh_uid = db.resolve_user_id(kesh_chat)

    prompt = "@9999988888 we have client sync at 4pm and @keshav_phone_test design the landing page"

    sent_tg = []
    async def mock_tg(cid, msg):
        sent_tg.append((cid, msg))

    with patch("main.send_telegram_text", side_effect=mock_tg):
        res = await execute_targeted_mentions_dispatch(sender_chat, prompt)

    assert res.status == "success"
    assert len(res.directives) == 2

    # Devender receives via Telegram
    assert len(sent_tg) == 1
    assert sent_tg[0][0] == dev_chat
    assert "client sync at 4pm" in sent_tg[0][1]
    assert "landing page" not in sent_tg[0][1]

    # Keshav receives via In-App Notification
    notifs = db.get_in_app_notifications(kesh_uid)
    assert len(notifs) >= 1
    assert "design the landing page" in notifs[0]["body"]
    assert "client sync at 4pm" not in notifs[0]["body"]


@pytest.mark.asyncio
async def test_unlinked_phone_number_reports_not_in_peculiar_ai_network():
    db._init_mentions_and_dispatches_tables()
    sender_chat = 99881120
    db.ensure_user(sender_chat)

    prompt = "@9461089998 u have the work on ui design"
    res = await execute_targeted_mentions_dispatch(sender_chat, prompt)
    assert res.status == "success"
    assert "not connected to the Peculiar AI network yet" in res.summary_message


@pytest.mark.asyncio
async def test_link_user_phone_number_and_subsequent_directed_dispatch():
    db._init_mentions_and_dispatches_tables()
    sender_chat = 99881121
    devender_chat = 99881122
    db.ensure_user(sender_chat)
    db.ensure_user(devender_chat)

    # Devender explicitly links his phone number
    ok, msg = db.set_user_phone_number(devender_chat, "9461089998")
    assert ok is True

    prompt = "@9461089998 u have the work on ui design"
    sent_tg = []
    async def mock_tg(cid, msg):
        sent_tg.append((cid, msg))

    with patch("main.send_telegram_text", side_effect=mock_tg):
        res = await execute_targeted_mentions_dispatch(sender_chat, prompt)

    assert res.status == "success"
    assert len(sent_tg) == 1
    assert sent_tg[0][0] == devender_chat
    assert "work on ui design" in sent_tg[0][1]


def test_phone_number_single_account_collision_and_update_and_unlink():
    db._init_mentions_and_dispatches_tables()
    user_1 = 99881130
    user_2 = 99881131
    db.ensure_user(user_1)
    db.ensure_user(user_2)

    # 1. User 1 links phone 8824466229
    ok1, msg1 = db.set_user_phone_number(user_1, "8824466229")
    assert ok1 is True
    assert "successfully linked" in msg1

    # 2. User 2 tries to link same phone 8824466229 (Collision Check)
    ok2, msg2 = db.set_user_phone_number(user_2, "8824466229")
    assert ok2 is False
    assert "already linked to another Peculiar AI account" in msg2

    # 3. User 1 updates / replaces their phone number to 8824466230
    ok3, msg3 = db.set_user_phone_number(user_1, "8824466230")
    assert ok3 is True
    assert "updated" in msg3

    # 4. Now phone 8824466229 is freed up, so User 2 can link 8824466229!
    ok4, msg4 = db.set_user_phone_number(user_2, "8824466229")
    assert ok4 is True

    # 5. User 1 unlinks their phone
    ok_unlink, msg_unlink = db.unlink_user_phone_number(user_1)
    assert ok_unlink is True
    assert "successfully unlinked" in msg_unlink

    # Unlink when already unlinked
    ok_unl2, _ = db.unlink_user_phone_number(user_1)
    assert ok_unl2 is False


def test_api_phone_link_and_unlink_endpoints():
    db._init_mentions_and_dispatches_tables()
    cid = 99881135
    db.ensure_user(cid)

    # Link via REST API
    res = client.post("/api/v1/user/phone/link", json={"phone_number": "9811223344"}, headers={"X-Chat-Id": str(cid)})
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    # Unlink via REST API
    res_unl = client.post("/api/v1/user/phone/unlink", headers={"X-Chat-Id": str(cid)})
    assert res_unl.status_code == 200
    assert res_unl.json()["status"] == "success"


# =============================================================================
# 7. Media Ingestion & Attachment Forwarding Tests
# =============================================================================

@pytest.mark.asyncio
async def test_mention_dispatch_delivers_file_attachment():
    import os
    from core import timeutil
    from core.timeutil import IST
    db._init_mentions_and_dispatches_tables()
    sender_chat = 99881140
    recip_chat = 99881141
    db.ensure_user(sender_chat)
    db.ensure_user(recip_chat)
    db.claim_username(sender_chat, "nirav_sender")
    db.set_user_phone_number(recip_chat, "9549408914")

    test_dir = os.path.abspath(os.path.join("static", "uploads", "telegram"))
    os.makedirs(test_dir, exist_ok=True)
    file_path = os.path.join(test_dir, "test_resignation.pdf")
    with open(file_path, "wb") as f:
        f.write(b"%PDF-1.4 test dummy resignation document bytes")

    from main import RECENT_DOCS
    RECENT_DOCS[sender_chat] = {
        "filename": "resignation.pdf",
        "content": "Resignation letter text",
        "path": file_path,
        "type": "document",
        "timestamp": timeutil.now(IST)
    }

    sent_files = []
    sent_texts = []

    async def mock_send_file(chat_id, filename, content_bytes=None, caption="", mime_type="application/octet-stream"):
        sent_files.append({"chat_id": chat_id, "filename": filename, "bytes": content_bytes, "caption": caption})

    async def mock_send_text(chat_id, text):
        sent_texts.append({"chat_id": chat_id, "text": text})

    with patch("main.send_file", side_effect=mock_send_file), \
         patch("main.send_telegram_text", side_effect=mock_send_text):
        res = await execute_targeted_mentions_dispatch(sender_chat, "@9549408914 Here is the resignation document.")

    assert res.status == "success"
    assert len(sent_texts) == 1
    assert sent_texts[0]["chat_id"] == recip_chat
    assert "Here is the resignation document" in sent_texts[0]["text"]

    assert len(sent_files) == 1
    assert sent_files[0]["chat_id"] == recip_chat
    assert sent_files[0]["filename"] == "resignation.pdf"
    assert sent_files[0]["bytes"] == b"%PDF-1.4 test dummy resignation document bytes"


@pytest.mark.asyncio
async def test_document_upload_without_caption_prompts_clean_inquiry():
    chat_id = 99881142
    db.ensure_user(chat_id)

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append(text)

    doc_payload = {
        "file_id": "doc_file_123",
        "file_name": "Quarterly_Report.pdf",
        "mime_type": "application/pdf"
    }

    with patch("main.process_telegram_document", new_callable=AsyncMock) as mock_ptd, \
         patch("main.say_raw", side_effect=mock_say), \
         patch("main.say", side_effect=mock_say):

        mock_ptd.return_value = {
            "file_name": "Quarterly_Report.pdf",
            "text": "Extensive 50 page report contents with tons of raw text...",
            "file_path": "/fake/path/Quarterly_Report.pdf",
            "is_empty": False
        }

        from main import process_document
        await process_document(chat_id, doc_payload, caption="")

    raw_responses = " ".join(sent_messages)
    assert "Quarterly_Report.pdf" in raw_responses
    assert "Extensive 50 page report contents" not in raw_responses
    assert any(w in raw_responses.lower() for w in ("analyze", "karna chahte", "what would you like"))


@pytest.mark.asyncio
async def test_photo_upload_without_caption_prompts_clean_inquiry():
    chat_id = 99881143
    db.ensure_user(chat_id)

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append(text)

    photos = [{"file_id": "photo_123"}]

    with patch("main.process_telegram_photo", new_callable=AsyncMock) as mock_ptp, \
         patch("main.say_raw", side_effect=mock_say), \
         patch("main.say", side_effect=mock_say):

        mock_ptp.return_value = "Detailed analysis of receipt with ₹5,000 electricity bill and 10 item breakdown..."

        from main import process_photo
        await process_photo(chat_id, photos, caption="")

    raw_responses = " ".join(sent_messages)
    assert "Detailed analysis of receipt with ₹5,000 electricity bill" not in raw_responses
    assert any(w in raw_responses.lower() for w in ("analyze", "karna chahte", "what would you like"))




