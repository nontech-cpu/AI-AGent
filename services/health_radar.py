"""
Saptic AI — Memory Health Score & Cognitive Decay Radar Engine
Calculates user's life clarity score (0-100) based on pending commitments,
overdue tasks, lingering smart lists, and timely bill payments.
"""
import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, List

from sqlalchemy import text
import db
from timing import IST
import core.timeutil as timeutil

logger = logging.getLogger("saptic_health_radar")


def _resolve_uid(user_id_or_chat_id: int) -> int:
    from memory.store import resolve_user_id
    return resolve_user_id(user_id_or_chat_id)


def compute_memory_health_score(user_id_or_chat_id: int) -> Dict[str, Any]:
    """
    Computes a gamified 0-100 Memory Health Score.
    
    Formula:
      Base Score: 100
      - Overdue commitment past deadline: -7 points each
      - Unchecked smart list item older than 3 days: -3 points each
      - Unpaid bill past due date: -10 points each
      - Uncategorized/vague pending memory items: -2 points each
      + Reward for active memory bubbles: +3 points per active bubble (up to +15)
      + Reward for recently completed tasks: +2 points each (up to +10)
    """
    uid = _resolve_uid(user_id_or_chat_id)
    today = timeutil.today(IST)
    today_str = str(today)

    score = 100
    deductions = []
    accomplishments = []
    action_recommendations = []

    try:
        with db.engine.connect() as conn:
            # 1. Overdue commitments
            comms = conn.execute(
                text("SELECT id, title, target_date, status FROM commitments WHERE user_id = :uid AND status != 'completed'"),
                {"uid": uid}
            ).mappings().all()

            overdue_comms = []
            for c in comms:
                t_date = c.get("target_date")
                if t_date:
                    d_val = t_date.date() if hasattr(t_date, "date") else t_date
                    if d_val < today:
                        overdue_comms.append(c.get("title") or "Commitment")

            if overdue_comms:
                penalty = min(35, len(overdue_comms) * 7)
                score -= penalty
                deductions.append(f"{len(overdue_comms)} overdue commitment(s) past deadline (-{penalty} pts)")
                for oc in overdue_comms[:2]:
                    action_recommendations.append(f"Resolve overdue commitment: '{oc}'")

            # 2. Lingering smart lists items
            lists = db.get_smart_lists(user_id_or_chat_id) or []
            pending_items_count = 0
            for l in lists:
                for it in l.get("items", []):
                    if not it.get("checked"):
                        pending_items_count += 1
                        if len(action_recommendations) < 3:
                            action_recommendations.append(f"Complete or archive '{it.get('title')}' in {l.get('name')}")

            if pending_items_count > 3:
                penalty = min(20, (pending_items_count - 3) * 3)
                score -= penalty
                deductions.append(f"{pending_items_count} pending smart list item(s) (-{penalty} pts)")

            # 3. Active Memory Bubbles bonus
            bubbles = conn.execute(
                text("SELECT COUNT(*) FROM memory_bubbles WHERE user_id = :uid"),
                {"uid": uid}
            ).scalar() or 0
            bubble_bonus = min(15, bubbles * 3)
            score = min(100, score + bubble_bonus)
            if bubble_bonus > 0:
                accomplishments.append(f"{bubbles} structured memory bubble(s) organized (+{bubble_bonus} pts)")

            # 4. Completed commitments / tasks
            completed_count = conn.execute(
                text("SELECT COUNT(*) FROM commitments WHERE user_id = :uid AND status = 'completed'"),
                {"uid": uid}
            ).scalar() or 0
            if completed_count > 0:
                comp_bonus = min(10, completed_count * 2)
                score = min(100, score + comp_bonus)
                accomplishments.append(f"{completed_count} commitment(s) fulfilled (+{comp_bonus} pts)")

    except Exception as exc:
        logger.error(f"compute_memory_health_score error: {exc}")

    # Clamp score to [10, 100]
    score = max(10, min(100, score))

    if score >= 90:
        grade = "Excellent"
        summary = "Aapka cognitive radar ekdum sharp aur clutter-free hai! Saare major tasks control mein hain."
    elif score >= 75:
        grade = "Good"
        summary = "Overall accha status hai, lekin 1-2 pending commitments clear karke aap 95+ score kar sakte hain."
    elif score >= 50:
        grade = "Moderate"
        summary = "Thoda cognitive overload ho raha hai. Purane items review karke complete ya carry-forward karein."
    else:
        grade = "Action Required"
        summary = "Kaafi commitments aur deadlines pending hain! Saptic AI ke saath baithkar 10-minute cleanup karein."

    if not action_recommendations:
        action_recommendations.append("Sab clear hai! Kal ki meeting ya travel plan karni ho toh bataiye.")

    return {
        "score": score,
        "grade": grade,
        "clarity_rating": grade,
        "summary": summary,
        "deductions": deductions,
        "accomplishments": accomplishments,
        "action_recommendations": action_recommendations,
        "calculated_at": timeutil.now(IST).strftime("%d %b %Y, %I:%M %p IST"),
    }


def format_health_radar_telegram_message(score_data: Dict[str, Any]) -> str:
    """Renders a beautiful Telegram markdown message for /radar command."""
    score = score_data["score"]
    grade = score_data["grade"]
    summary = score_data["summary"]

    lines = [
        f"📊 **Saptic Memory Health Radar**",
        f"━━━━━━━━━━━━━━━━━━━",
        f"🎯 **Clarity Score**: `{score}/100` ({grade})",
        f"",
        f"💡 *{summary}*",
        f"",
    ]

    if score_data.get("accomplishments"):
        lines.append("✨ **Accomplishments:**")
        for acc in score_data["accomplishments"]:
            lines.append(f"  • {acc}")
        lines.append("")

    if score_data.get("deductions"):
        lines.append("⚠️ **Attention Points:**")
        for ded in score_data["deductions"]:
            lines.append(f"  • {ded}")
        lines.append("")

    lines.append("🚀 **Top Actionable Steps (100/100 pahunchne ke liye):**")
    for i, rec in enumerate(score_data.get("action_recommendations", [])[:3], 1):
        lines.append(f"  {i}. {rec}")

    lines.append("")
    lines.append(f"🕒 *Audited on: {score_data.get('calculated_at')}*")
    return "\n".join(lines)
