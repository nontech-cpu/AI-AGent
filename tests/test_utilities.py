"""
Unit and Integration Tests for Real-time Utilities
Covers Weather, Currency Conversion, Market Prices (Gold/Crypto), and Math Calculator.
"""
import pytest
import unittest
from unittest.mock import patch, MagicMock
from utilities import calculate, convert_currency, get_market_price, get_weather
from agent.tools import execute_tool, TOOL_DEFINITIONS, GetWeatherArgs, ConvertCurrencyArgs, GetMarketPriceArgs, CalculateArgs


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
    res = await get_weather("Manali")
    assert res["status"] == "success"
    assert "Manali" in res["location"]
    assert "temperature" in res


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


def test_tool_definitions_presence():
    tool_names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
    for required in ["get_weather", "convert_currency", "get_market_price", "calculate"]:
        assert required in tool_names, f"Tool {required} missing from TOOL_DEFINITIONS"
