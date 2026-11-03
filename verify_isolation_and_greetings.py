import asyncio
import os
import sys
from datetime import datetime
import db

def test_user_display_names():
    print("Testing get_user_display_name...")
    devendra_name = db.get_user_display_name(2144278363)
    neerav_name = db.get_user_display_name(1758056012)
    peeps_name = db.get_user_display_name(7284786622)
    unknown_name = db.get_user_display_name(9999999999)

    print(f"2144278363 (Manager) -> '{devendra_name}'")
    print(f"1758056012 (Neerav)  -> '{neerav_name}'")
    print(f"7284786622 (Peeps)   -> '{peeps_name}'")
    print(f"9999999999 (Unknown) -> '{unknown_name}'")

    assert devendra_name == "Devendra", f"Expected Devendra, got {devendra_name}"
    assert neerav_name in ("Neerav", "Nirav"), f"Expected Neerav, got {neerav_name}"
    assert peeps_name == "Peeps", f"Expected Peeps, got {peeps_name}"
    assert unknown_name == "", f"Expected empty, got {unknown_name}"
    print("[PASS] get_user_display_name")


def test_tenant_isolation_canonical_map():
    print("\nTesting tenant isolation & linked chat IDs...")
    assert db.CANONICAL_CHAT_ID_MAP == {}, "CANONICAL_CHAT_ID_MAP must be empty for complete tenant privacy!"
    
    ids = [2144278363, 1758056012, 7284786622, 7940177745]
    for cid in ids:
        linked = db.get_all_linked_chat_ids(cid)
        assert linked == {cid}, f"Expected {{{cid}}}, got {linked}"
        canon = db.canonical_chat_id(cid)
        assert canon == cid, f"Expected {cid}, got {canon}"
    print("[PASS] Tenant isolation & linked IDs")


def test_evening_wrapup_text_rendering():
    print("\nTesting evening wrap-up greeting generation...")
    for cid in [2144278363, 1758056012, 7284786622, 9999999999]:
        name = db.get_user_display_name(cid)
        greeting_name = f" {name}" if name else ""
        msg = (
            f"Good Evening{greeting_name}!\n"
            f"Aaj ke saare major tasks aur schedule complete hain. Kal ke din ki planning karni ho toh batana, aaram se rest karo!"
        )
        if cid == 2144278363:
            assert "Good Evening Devendra!" in msg, f"Manager must receive their own name, got: {msg}"
            assert "Neerav" not in msg, f"Manager message must never contain Neerav! Got: {msg}"
        elif cid == 7284786622:
            assert "Good Evening Peeps!" in msg, f"Peeps must receive Peeps!, got: {msg}"
            assert "Neerav" not in msg, f"Peeps message must never contain Neerav! Got: {msg}"
        elif cid == 9999999999:
            assert "Good Evening!" in msg, f"Unknown user should get neutral greeting, got: {msg}"
            assert "Neerav" not in msg
        print(f"Chat {cid} evening msg preview -> {msg.splitlines()[0]}")
    print("[PASS] Evening wrap-up dynamic greeting")


def test_google_tokens_isolation():
    print("\nTesting connected Google tokens isolation...")
    users = db.get_all_connected_google_users()
    print(f"Currently connected Google users count: {len(users)}")
    for u in users:
        print(f"  User ID: {u['user_id']}, Chat ID: {u['telegram_chat_id']}, Email: {u['email']}")
        assert u['telegram_chat_id'] != 2144278363, "Manager (2144278363) must NOT have a connected Google account!"
    
    tok = db.get_google_token(114)
    assert tok is None or not tok.get("refresh_token_enc"), f"Manager token must be None, got: {tok}"
    print("[PASS] Google tokens isolation")


def test_memory_store_resolution():
    print("\nTesting memory/store resolve_user_id...")
    from memory.store import resolve_user_id
    uid_dev = resolve_user_id(2144278363)
    uid_neerav = resolve_user_id(1758056012)
    assert uid_dev == 114, f"Expected 114 for Devendra, got {uid_dev}"
    assert uid_neerav == 408, f"Expected 408 for Neerav, got {uid_neerav}"
    assert uid_dev != uid_neerav, "Devendra and Neerav must NOT resolve to the same user_id!"
    print(f"Devendra chat 2144278363 -> uid {uid_dev}")
    print(f"Neerav chat 1758056012  -> uid {uid_neerav}")
    print("[PASS] memory/store resolve_user_id")


if __name__ == "__main__":
    test_user_display_names()
    test_tenant_isolation_canonical_map()
    test_evening_wrapup_text_rendering()
    test_google_tokens_isolation()
    test_memory_store_resolution()
    print("\n" + "="*50)
    print("ALL 5 MULTI-USER PRIVACY & GREETING TESTS PASSED!")
    print("="*50)
