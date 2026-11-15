"""
Unit Tests for Industrial Clean Onboarding Pipeline:
1. Consent & Google Link Dynamic Recommendation
2. Language Preference Selection & Locking
3. City / Home Town Location State Machine & Cognitive Memory Ingestion
4. Persona Selection & Personalized Setup Completion
5. Zero-Friction City Auto-Inference for Local Queries & Weather
"""

import pytest
import asyncio
from unittest.mock import patch, AsyncMock
import db
import main
from utilities import clean_city_query, resolve_inferred_city


@pytest.mark.asyncio
async def test_google_link_recommendation_dynamic():
    chat_id_unlinked = 888111001
    chat_id_linked = 888111002

    db.ensure_user(chat_id_unlinked)
    db.ensure_user(chat_id_linked)

    # Mock get_valid_google_connection_by_chat_id
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_google:
        # Unlinked user
        mock_google.return_value = (False, None)
        text_unlinked = await main.get_welcome_consent_text(chat_id_unlinked)
        assert "[Recommended: Please connect and link your Google account to ensure a smooth flow]" in text_unlinked

        # Linked user
        mock_google.return_value = (True, "user@gmail.com")
        text_linked = await main.get_welcome_consent_text(chat_id_linked)
        assert "[Recommended: Please connect and link your Google account to ensure a smooth flow]" not in text_linked


@pytest.mark.asyncio
async def test_onboarding_pipeline_step_by_step():
    test_chat = 888222333
    db.ensure_user(test_chat)

    sent_messages = []

    async def mock_say(chat_id, text, **kwargs):
        sent_messages.append((chat_id, text))

    with patch("main.say", side_effect=mock_say):
        # 1. User says Yes to Consent
        main.set_state(test_chat, "consent")
        await main.handle_user_text(test_chat, "yes")
        
        assert main.has_consent(test_chat) is True
        state, _ = main.get_state(test_chat)
        assert state == "onboarding_language"
        assert any("Aap kis language me" in m[1] for m in sent_messages)

        # 2. User selects Language (Hinglish)
        sent_messages.clear()
        await main.handle_user_text(test_chat, "1")
        
        pref = db.get_user_preference(test_chat)
        assert pref.get("reply_language") == "hinglish"
        state, _ = main.get_state(test_chat)
        assert state == "onboarding_city"
        assert any("Aap kis city (shehar) me rehte hain?" in m[1] for m in sent_messages)

        # 3. User provides City ("jodhpur me rehta hu")
        sent_messages.clear()
        await main.handle_user_text(test_chat, "jodhpur me rehta hu")
        
        # Verify City saved in location state machine, cognitive facts, and preferences
        loc_state = db.get_user_location_state(test_chat)
        assert loc_state is not None
        assert loc_state["current_city"].lower() == "jodhpur"

        facts = db.get_user_facts(test_chat)
        assert any("Jodhpur" in f["fact"] for f in facts)

        # Verify city auto-inference works for weather/places without asking again
        inferred = resolve_inferred_city(test_chat)
        assert inferred == "Jodhpur"

        state, _ = main.get_state(test_chat)
        assert state == "persona"

        # 4. User selects Persona (Job)
        sent_messages.clear()
        await main.handle_user_text(test_chat, "1")
        
        state, _ = main.get_state(test_chat)
        assert state is None  # Setup complete
        assert any("Setup Complete!" in m[1] for m in sent_messages)
        assert any("Jodhpur" in m[1] for m in sent_messages)
        assert any("Hinglish" in m[1] for m in sent_messages)
