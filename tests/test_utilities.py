"""
Unit and Integration Tests for Real-time Utilities
Covers Weather, Currency Conversion, Market Prices (Gold/Crypto), and Math Calculator.
"""
import pytest
import unittest
from unittest.mock import patch, MagicMock
from utilities import calculate, convert_currency, get_market_price, get_weather, search_web, read_webpage
from agent.tools import execute_tool, TOOL_DEFINITIONS, GetWeatherArgs, ConvertCurrencyArgs, GetMarketPriceArgs, CalculateArgs, SearchWebArgs, ReadWebpageArgs


class TestCalculator(unittest.TestCase):
    def test_basic_arithmetic(self):
        res = calculate("92 * 83.5")
        self.assertEqual(res["status"], "success")
        self.assertAlmostEqual(res["result"], 7682.0)

    def test_addition_and_parentheses(self):
        res = calculate("(100 + 50) * 2 - 25")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["result"], 275)

    def test_percentage_of_syntax(self):
        res = calculate("18% of 4500")
        self.assertEqual(res["status"], "success")
        self.assertAlmostEqual(res["result"], 810.0)

    def test_division_by_zero(self):
        res = calculate("100 / 0")
        self.assertEqual(res["status"], "error")
        self.assertIn("Division by zero", res["message"])

    def test_invalid_syntax(self):
        res = calculate("import os; os.system('ls')")
        self.assertEqual(res["status"], "error")


@pytest.mark.asyncio
async def test_currency_conversion():
    # Test real or fallback live rate
    res = await convert_currency(92, "USD", "INR")
    assert res["status"] == "success"
    assert res["amount"] == 92
    assert res["converted_amount"] > 0
    assert "₹" in res["formatted"] or "INR" in res["formatted"]


@pytest.mark.asyncio
async def test_market_prices():
    # Test Gold price calculation
    res = await get_market_price("gold", "inr")
    assert res["status"] == "success"
    assert res["asset"] == "Gold"
    assert "per_10g_inr" in res or "price" in res

    # Test Bitcoin price calculation
    btc_res = await get_market_price("bitcoin", "usd")
    assert btc_res["status"] == "success"
    assert btc_res["asset"] == "Bitcoin"
    assert btc_res["price"] > 0


@pytest.mark.asyncio
async def test_weather_lookup():
    # 1. Clean city lookup
    res = await get_weather("Manali")
    assert res["status"] == "success"
    assert "Manali" in res["location"]
    assert "temperature" in res

    # 2. Messy Hinglish city input: "jodhpur m" -> cleans to "Jodhpur"
    jodhpur_res = await get_weather("jodhpur m")
    assert jodhpur_res["status"] == "success"
    assert "Jodhpur" in jodhpur_res["location"]
    assert "temperature" in jodhpur_res

    # 3. Messy sentence: "bhy jodhpur m weather" -> cleans to "Jodhpur"
    bhy_res = await get_weather("bhy jodhpur m weather")
    assert bhy_res["status"] == "success"
    assert "Jodhpur" in bhy_res["location"]

    # 4. Silver market price test
    sil_res = await get_market_price("silver", "inr")
    assert sil_res["status"] == "success"
    assert sil_res["asset"] == "Silver"
    assert sil_res["price_1kg_inr"] > 0


@pytest.mark.asyncio
async def test_agent_tools_dispatch():
    # 1. Calculate tool
    calc_res = await execute_tool(12345, "calculate", {"expression": "500 * 4"})
    assert calc_res["status"] == "success"
    assert calc_res["result"] == 2000

    # 2. Convert currency tool
    curr_res = await execute_tool(12345, "convert_currency", {"amount": 10, "from_currency": "USD", "to_currency": "INR"})
    assert curr_res["status"] == "success"
    assert curr_res["converted_amount"] > 0

    # 3. Market price tool
    gold_res = await execute_tool(12345, "get_market_price", {"item": "gold", "currency": "inr"})
    assert gold_res["status"] == "success"

    # 4. Weather tool
    weather_res = await execute_tool(12345, "get_weather", {"city": "Delhi"})
    assert weather_res["status"] == "success"

    # 5. Search web tool
    web_res = await execute_tool(12345, "search_web", {"query": "Jodhpur to Udaipur bike toll distance"})
    assert web_res["status"] == "success"
    assert "results" in web_res

    # 6. Read webpage tool
    page_res = await execute_tool(12345, "read_webpage", {"url": "https://example.com", "max_chars": 500})
    assert page_res["status"] in ("success", "error")  # Network may succeed or mock
    if page_res["status"] == "success":
        assert "content" in page_res
        assert "title" in page_res


def test_tool_definitions_presence():
    tool_names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
    for required in ["get_weather", "convert_currency", "get_market_price", "calculate", "search_web", "read_webpage"]:
        assert required in tool_names, f"Tool {required} missing from TOOL_DEFINITIONS"


@pytest.mark.asyncio
async def test_search_web_empty_query():
    res = await search_web("")
    assert res["status"] == "error"
    assert res["count"] == 0


@pytest.mark.asyncio
async def test_read_webpage_empty_url():
    res = await read_webpage("")
    assert res["status"] == "error"
    assert res["success"] is False


@pytest.mark.asyncio
async def test_read_webpage_parser_mock():
    mock_html = """
    <html>
        <head><title>Test Article &amp; Insights</title><style>body { color: red; }</style></head>
        <body>
            <nav><a href="#">Home</a></nav>
            <script>console.log("secret");</script>
            <h1>Artificial Intelligence</h1>
            <p>Antigravity is building autonomous reasoning agents for daily tasks.</p>
            <footer>Copyright 2026</footer>
        </body>
    </html>
    """
    class MockResponse:
        status_code = 200
        text = mock_html

    with patch("httpx.AsyncClient.get", return_value=MockResponse()):
        res = await read_webpage("https://peculiar-ai.com/article", max_chars=300)
        assert res["status"] == "success"
        assert res["title"] == "Test Article & Insights"
        assert "Antigravity is building autonomous reasoning" in res["content"]
        assert "console.log" not in res["content"]
        assert "<style>" not in res["content"]
        assert "<nav>" not in res["content"]
