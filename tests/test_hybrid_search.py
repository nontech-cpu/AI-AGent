import pytest
from unittest.mock import AsyncMock, patch
from utilities import classify_search_intent, search_web, _search_tavily, _search_serper


def test_classify_search_intent_places_vs_research():
    # Local & Geospatial queries -> local_places
    assert classify_search_intent("best cafe in jaipur") == "local_places"
    assert classify_search_intent("zudio showroom in kota") == "local_places"
    assert classify_search_intent("hospitals near me") == "local_places"
    assert classify_search_intent("delhi to jaipur highway toll distance") == "local_places"
    assert classify_search_intent("nearest metro station in delhi") == "local_places"

    # Knowledge, Research & Documentation -> knowledge_research
    assert classify_search_intent("latest python 3.14 release date") == "knowledge_research"
    assert classify_search_intent("how to configure docker compose v2") == "knowledge_research"
    assert classify_search_intent("difference between sync and async in python") == "knowledge_research"
    assert classify_search_intent("history of jaipur forts") == "knowledge_research"
    assert classify_search_intent("population of mumbai") == "knowledge_research"


@pytest.mark.asyncio
async def test_search_web_empty_query():
    res = await search_web("   ")
    assert res["status"] == "error"
    assert res["success"] is False
    assert res["count"] == 0


@pytest.mark.asyncio
async def test_hybrid_search_routes_research_to_tavily():
    mock_tavily_data = {
        "results": [
            {"title": "Python 3.14 Overview", "snippet": "New features in Python 3.14...", "url": "https://python.org"}
        ],
        "answer": "Python 3.14 introduces several performance improvements.",
        "engine": "tavily"
    }

    with patch("utilities._search_tavily", new_callable=AsyncMock, return_value=mock_tavily_data) as mock_tav, \
         patch("utilities._search_serper", new_callable=AsyncMock) as mock_serp:

        res = await search_web("latest python 3.14 updates")
        assert res["status"] == "success"
        assert res["engine"] == "tavily"
        assert res["intent"] == "knowledge_research"
        assert res["answer"] == "Python 3.14 introduces several performance improvements."
        assert len(res["results"]) == 1
        assert "💡 Summary:" in res["message"]
        mock_tav.assert_called_once()
        mock_serp.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_search_routes_local_places_to_google_places():
    mock_places_data = {
        "results": [
            {"title": "📍 Curious Life Coffee Roasters (Rating: 4.6★)", "snippet": "Address: C Scheme, Jaipur", "url": "https://maps.google.com"}
        ],
        "answer": None,
        "engine": "google_places"
    }

    with patch("utilities._search_serper_places", new_callable=AsyncMock, return_value=mock_places_data) as mock_places, \
         patch("utilities._search_serper", new_callable=AsyncMock) as mock_serp, \
         patch("utilities._search_tavily", new_callable=AsyncMock) as mock_tav:

        res = await search_web("best cafe in jaipur")
        assert res["status"] == "success"
        assert res["engine"] == "google_places"
        assert res["intent"] == "local_places"
        assert len(res["results"]) == 1
        mock_places.assert_called_once()
        mock_serp.assert_not_called()
        mock_tav.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_search_routes_local_places_to_google_serper_when_places_empty():
    mock_serper_data = {
        "results": [
            {"title": "📍 Curious Life Coffee Roasters (Rating: 4.6★)", "snippet": "Address: C Scheme, Jaipur", "url": "https://maps.google.com"}
        ],
        "answer": None,
        "engine": "serper_google"
    }

    with patch("utilities._search_serper_places", new_callable=AsyncMock, return_value=None) as mock_places, \
         patch("utilities._search_serper", new_callable=AsyncMock, return_value=mock_serper_data) as mock_serp, \
         patch("utilities._search_tavily", new_callable=AsyncMock) as mock_tav:

        res = await search_web("best cafe in jaipur")
        assert res["status"] == "success"
        assert res["engine"] == "serper_google"
        assert res["intent"] == "local_places"
        assert len(res["results"]) == 1
        mock_places.assert_called_once()
        mock_serp.assert_called_once()
        mock_tav.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_search_fallback_when_primary_fails():
    # If Tavily fails (e.g. rate limit / network error), Serper takes over automatically
    mock_serper_data = {
        "results": [
            {"title": "FastAPI Guide", "snippet": "Production async framework", "url": "https://fastapi.tiangolo.com"}
        ],
        "answer": None,
        "engine": "serper_google"
    }

    with patch("utilities._search_tavily", new_callable=AsyncMock, return_value=None) as mock_tav, \
         patch("utilities._search_serper", new_callable=AsyncMock, return_value=mock_serper_data) as mock_serp:

        res = await search_web("fastapi production deployment")
        assert res["status"] == "success"
        assert res["engine"] == "serper_google"
        assert len(res["results"]) == 1
        mock_tav.assert_called_once()
        mock_serp.assert_called_once()


@pytest.mark.asyncio
async def test_hybrid_search_duckduckgo_fallback_when_both_keys_missing():
    mock_ddg_data = {
        "results": [
            {"title": "Open Source Python", "snippet": "General info", "url": "https://duckduckgo.com"}
        ],
        "answer": None,
        "engine": "duckduckgo"
    }

    with patch("utilities._search_tavily", new_callable=AsyncMock, return_value=None), \
         patch("utilities._search_serper", new_callable=AsyncMock, return_value=None), \
         patch("utilities._search_duckduckgo", new_callable=AsyncMock, return_value=mock_ddg_data):

        res = await search_web("general query with no api keys")
        assert res["status"] == "success"
        assert res["engine"] == "duckduckgo"
        assert len(res["results"]) == 1


@pytest.mark.asyncio
async def test_find_nearby_places_prioritizes_google_places():
    from utilities import find_nearby_places
    mock_places_data = {
        "results": [
            {
                "title": "📍 Reliance Smart Point ★4.2",
                "snippet": "Address: Ground floor, Geeta Bhawan Rd, Jodhpur. Type: Department store",
                "url": "https://maps.google.com",
                "address": "Ground floor, Geeta Bhawan Rd, Jodhpur"
            }
        ],
        "answer": None,
        "engine": "google_places"
    }

    with patch("utilities._search_serper_places", new_callable=AsyncMock, return_value=mock_places_data) as mock_p:
        res = await find_nearby_places(category="Smart Point", city="Jodhpur")
        assert res["status"] == "success"
        assert res["city"] == "Jodhpur"
        assert len(res["results"]) == 1
        assert "Geeta Bhawan Rd" in res["message"]
        mock_p.assert_called_once()


def test_tool_metadata_find_nearby_places_bidirectional_alias():
    from agent.tool_metadata import validate_tool_call_parameters

    # Category provided -> automatically aliases to query
    ok, err = validate_tool_call_parameters("find_nearby_places", {"category": "Smart Point", "city": "Jodhpur"})
    assert ok is True
    assert err is None

    # Query provided -> automatically aliases to category
    ok, err = validate_tool_call_parameters("find_nearby_places", {"query": "Smart Point", "city": "Jodhpur"})
    assert ok is True
    assert err is None

    # Neither provided -> returns missing query
    ok, err = validate_tool_call_parameters("find_nearby_places", {"city": "Jodhpur"})
    assert ok is False
    assert err["missing"] == ["query"]

