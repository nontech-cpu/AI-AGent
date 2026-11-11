import pytest
from state.pending import classify_confirmation_intent, STRUCTURAL_CONFIRM_COMMANDS

def test_pending_action_not_hijacked_by_conversational_question():
    pending_action = {
        "id": 83,
        "action_kind": "create_reminder",
        "payload": {
            "kind": "create_reminder",
            "title": "Watch movie",
            "due_date": "2026-11-12",
            "due_time": "10:00"
        }
    }
    
    # Scenario A: Assistant asked conversational question about sugar reading
    last_assistant_sugar_msg = (
        "Sure thing! I'll remind you to check your sugar at 10 AM tomorrow — "
        "do you also want a quick follow-up reminder later that day to send me the reading?"
    )
    
    intent, extra = classify_confirmation_intent("yes", pending_action, last_assistant_text=last_assistant_sugar_msg)
    # MUST NOT confirm 'Watch movie'
    assert intent == "unrelated"
    
    # Scenario B: Assistant genuinely asked to confirm the movie or mentioned conflict
    last_assistant_movie_msg = (
        "Schedule Buffer: 'Review PHP project architecture' (gap 30 min). "
        "Kya aap 'Watch movie' reminder confirm karna chahenge?"
    )
    intent, extra = classify_confirmation_intent("yes", pending_action, last_assistant_text=last_assistant_movie_msg)
    # MUST confirm 'Watch movie'
    assert intent == "confirm"

    # Scenario C: Assistant asked about time change / conflict
    last_assistant_clash_msg = "Time Conflict: 'Review PHP' ke saath clash hai. Kya main ise confirm kar doon?"
    intent, extra = classify_confirmation_intent("yes", pending_action, last_assistant_text=last_assistant_clash_msg)
    assert intent == "confirm"
