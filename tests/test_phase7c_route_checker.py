import pytest
from unittest.mock import patch, AsyncMock
from agent.verifier import verify_and_ground_reply, audit_claims_with_llm

@pytest.mark.asyncio
async def test_route_claim_intercepted_without_nav_tool():
    """
    Phase 7c Item 7:
    Structured claim-checker detecting claims_route_or_travel_time intercepts
    ungrounded route predictions and returns honest Google Maps link.
    """
    draft = "Ratanada se Sardarpura Circuit House Road hote hue 10-15 minute lagenge."
    user_query = "ratanada se sardarpura best route kya hai"
    
    # 1. Audit claims: structured checker flags route claim
    with patch("llm.provider.chat", new=AsyncMock(return_value=type("Resp", (), {
        "text": '{"claims_action_done": false, "which_action": null, "claims_external_fact": true, "claims_route_or_travel_time": true, "sourced_by_tool": false, "reason": "Claims specific road route and transit duration"}'
    })())):
        audit = await audit_claims_with_llm(draft, "", user_query)
        assert audit.get("claims_route_or_travel_time") is True
        
        # 2. verify_and_ground_reply without navigation tool
        passed, revised, violations = await verify_and_ground_reply(
            draft_reply=draft,
            tool_results=[],
            user_query=user_query
        )
        assert passed is False
        assert "Ungrounded route or travel time claimed" in violations[0]
        assert "Google Maps" in revised
        assert "https://www.google.com/maps/dir/?api=1" in revised


@pytest.mark.asyncio
async def test_route_claim_allowed_when_sourced_by_nav_tool():
    """
    Phase 7c Item 7:
    When a verified navigation tool result is present in evidence, route passes.
    """
    draft = "Google Maps ke anusaar Circuit House Road se 12 minute lagenge."
    user_query = "ratanada se sardarpura route"
    
    with patch("llm.provider.chat", new=AsyncMock(return_value=type("Resp", (), {
        "text": '{"claims_action_done": false, "which_action": null, "claims_external_fact": true, "claims_route_or_travel_time": true, "sourced_by_tool": true, "reason": "Sourced from Google Maps directions tool"}'
    })())):
        passed, reply, violations = await verify_and_ground_reply(
            draft_reply=draft,
            tool_results=[{"tool_name": "google_maps", "status": "success", "duration": "12 mins"}],
            user_query=user_query
        )
        assert passed is True
        assert len(violations) == 0
        assert "12 minute" in reply

