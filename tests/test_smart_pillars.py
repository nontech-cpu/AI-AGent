"""
End-to-end verification for the 4 Smart Pillars:
1. Relationship Knowledge Graph (People CRM)
2. Subscription & Free-Trial Expiry Watchdog
3. Memory Health Score & Cognitive Decay Radar
4. Shared Memory Bubbles (with multi-tenant isolation)
"""
import sys
import os
import asyncio
from datetime import datetime, date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import db
from timing import IST
from services.relationship_service import (
    add_or_update_relationship,
    get_user_relationships,
    resolve_relationship_in_text,
    format_relationships_context,
    get_upcoming_birthdays,
)
from services.subscription_service import (
    upsert_subscription,
    get_user_subscriptions,
    check_expiring_subscriptions,
)
from services.health_radar import (
    compute_memory_health_score,
    format_health_radar_telegram_message,
)
from services.shared_bubble_service import (
    create_bubble_share_invite,
    join_shared_bubble,
    get_shared_bubbles_for_user,
)
from email_intelligence import analyze_incoming_email


import pytest
import core.timeutil as timeutil

TEST_USER_A = 998877661
TEST_USER_B = 998877662


@pytest.mark.asyncio
async def test_all_smart_pillars():
    print("=" * 60)
    print("SAPTIC AI - 4 SMART PILLARS VERIFICATION")
    print("=" * 60)

    # -------------------------------------------------------------
    # Pillar 1: Relationship Knowledge Graph (People CRM)
    # -------------------------------------------------------------
    print("\n[Pillar 1] Testing Relationship Knowledge Graph (People CRM)...")
    today = datetime.now(IST).date()
    bday_tomorrow = today + timedelta(days=1)
    bday_str = bday_tomorrow.strftime("%Y-%m-%d")

    # Add relationships
    r1 = add_or_update_relationship(
        TEST_USER_A,
        name="Ashok Kumar Sharma",
        relationship="Father",
        phone="+919829000001",
        birthday=bday_str,
        notes="Loves gardening"
    )
    assert r1.get("status") == "success", f"Failed to add father: {r1}"

    r2 = add_or_update_relationship(
        TEST_USER_A,
        name="Devendra Singh",
        relationship="Manager",
        phone="+919829000002",
        birthday="1988-12-10",
        notes="Engineering Lead"
    )
    assert r2.get("status") == "success", f"Failed to add manager: {r2}"

    # Query all
    all_rels = get_user_relationships(TEST_USER_A)
    assert len(all_rels) >= 2, f"Expected at least 2 rels, got {len(all_rels)}"

    # Resolve entity in text
    res_papa = resolve_relationship_in_text(TEST_USER_A, "Kal papa ka call aaya tha")
    assert any(x["relationship"].lower() == "father" for x in res_papa), "Failed to resolve 'papa' to father"

    res_mgr = resolve_relationship_in_text(TEST_USER_A, "Manager ko report submit karni hai")
    assert any("devendra" in x["name"].lower() for x in res_mgr), "Failed to resolve 'manager' to Devendra"

    # Birthday watchdog
    upcoming_bdays = get_upcoming_birthdays(TEST_USER_A, days_ahead=2)
    assert any("ashok" in b["name"].lower() for b in upcoming_bdays), "Failed to detect father's upcoming birthday"

    # Prompt context string
    ctx_str = format_relationships_context(TEST_USER_A)
    assert "Ashok Kumar Sharma" in ctx_str and "Devendra Singh" in ctx_str, f"Prompt context missing names: {ctx_str}"
    print("[PASS] People CRM: Entity resolution, prompt injection & birthday watchdog verified.")

    # -------------------------------------------------------------
    # Pillar 2: Subscription & Free-Trial Expiry Watchdog
    # -------------------------------------------------------------
    print("\n[Pillar 2] Testing Subscription & Free-Trial Watchdog...")
    # 1. Upsert a trial expiring in 2 days
    trial_exp = today + timedelta(days=2)
    s1 = upsert_subscription(
        TEST_USER_A,
        service_name="Netflix Ultra HD",
        amount=649.0,
        billing_cycle="monthly",
        is_trial=True,
        trial_expiry_date=trial_exp.strftime("%Y-%m-%d"),
        cancel_url="https://netflix.com/cancel"
    )
    assert s1.get("status") == "success", f"Failed to upsert trial: {s1}"

    # 2. Check expiring
    expiring = check_expiring_subscriptions(TEST_USER_A, days_ahead=3)
    assert any(x["service"] == "Netflix Ultra HD" and x["kind"] == "trial_expiring" for x in expiring), f"Expiring trial not detected: {expiring}"

    # 3. Test email intelligence detection of subscription/trial
    email_sub = await analyze_incoming_email(
        sender="billing@spotify.com",
        subject="Your Spotify Premium free trial is ending soon",
        body="Hi Neerav, your 30-day free trial will end on 2026-11-05. You will be billed ₹119/month unless you cancel before then at https://spotify.com/account/cancel."
    )
    assert email_sub.get("is_important") is True, "Spotify email should be marked important"
    assert email_sub.get("category") == "subscription_watchdog", f"Category mismatch: {email_sub.get('category')}"
    assert "Spotify" in (email_sub.get("alert_text") or ""), "Alert text should mention Spotify"
    print("[PASS] Subscription Watchdog: Free-trial tracking, expiry alerts & email intelligence verified.")

    # -------------------------------------------------------------
    # Pillar 3: Memory Health Score & Cognitive Decay Radar
    # -------------------------------------------------------------
    print("\n[Pillar 3] Testing Memory Health Score & Cognitive Decay Radar...")
    score_data = compute_memory_health_score(TEST_USER_A)
    assert "score" in score_data, "Score data missing 'score'"
    assert 0 <= score_data["score"] <= 100, f"Score out of bounds: {score_data['score']}"
    assert "clarity_rating" in score_data, "Missing clarity rating"

    tg_msg = format_health_radar_telegram_message(score_data)
    assert "Clarity Score" in tg_msg, "Telegram message missing header"
    print(f"[PASS] Health Radar computed score: {score_data['score']}/100 ({score_data['clarity_rating']}).")

    # -------------------------------------------------------------
    # Pillar 4: Shared Memory Bubbles
    # -------------------------------------------------------------
    print("\n[Pillar 4] Testing Shared Memory Bubbles & Multi-Tenant Isolation...")
    # Clean up prior test membership for repeatability
    with db.engine.begin() as conn:
        conn.execute(db.text("DELETE FROM shared_bubble_members WHERE shared_with_chat_id = :cid"), {"cid": TEST_USER_B})

    # Ensure bubble exists for USER A
    db.create_memory_bubble(TEST_USER_A, "work_project", "Project Apollo", "#6366F1", "Rocket")

    # Create invite code for work_project
    inv_res = create_bubble_share_invite(TEST_USER_A, "work_project", permission="read_write")
    invite_code = inv_res.get("invite_code") if isinstance(inv_res, dict) else inv_res
    assert invite_code.startswith("SAPTIC-"), f"Unexpected invite code format: {invite_code}"

    # User B joins using the invite code
    join_res = join_shared_bubble(TEST_USER_B, invite_code)
    assert join_res.get("status") == "success", f"User B failed to join bubble: {join_res}"

    # List shared bubbles for User B
    b_shared = get_shared_bubbles_for_user(TEST_USER_B)
    assert any(x["bubble_slug"] == "work_project" for x in b_shared), f"User B cannot see shared bubble: {b_shared}"

    # Multi-Tenant Isolation Check: User B MUST NOT see User A's private data or relationships
    b_rels = get_user_relationships(TEST_USER_B)
    assert not any("ashok" in r["name"].lower() for r in b_rels), "DATA LEAK: User B can see User A's father!"

    b_subs = get_user_subscriptions(TEST_USER_B)
    assert not any(s["service_name"] == "Netflix Ultra HD" for s in b_subs), "DATA LEAK: User B can see User A's subscriptions!"

    print("[PASS] Shared Bubbles: Invite generation, joining & strict multi-tenant isolation verified.")

    print("\n" + "=" * 60)
    print("ALL 4 SMART PILLARS VERIFIED SUCCESSFULLY! [100% PASS]")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_all_smart_pillars())
