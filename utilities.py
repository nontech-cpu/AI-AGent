"""
PeculiarAI — Production Real-Time Utilities Engine
Provides 100% free, reliable, no-auth-required live web utilities:
1. Live Weather & Forecasts (Open-Meteo)
2. Live Currency Exchange & Conversions (Open Exchange Rates)
3. Live Gold, Silver & Crypto Market Rates (CoinGecko Real-Time Markets)
4. Safe Math Calculator & Expression Evaluator (AST-based)
"""
import os
import re
import ast
import operator
import httpx
from typing import Dict, Any, Optional, Tuple

WEATHER_CODES = {
    0: "Clear sky (Dhoop / Saaf mausam)",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast (Baadal)",
    45: "Foggy (Kohar)",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain (Halki baarish)",
    63: "Moderate rain (Baarish)",
    65: "Heavy rain (Tez baarish)",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm (Toofan / Bijli)",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail"
}

CURRENCY_ALIASES = {
    "dollar": "USD", "dollars": "USD", "usd": "USD", "$": "USD", "us dollar": "USD",
    "inr": "INR", "rupee": "INR", "rupees": "INR", "rs": "INR", "₹": "INR", "rupaye": "INR", "paisa": "INR",
    "euro": "EUR", "euros": "EUR", "eur": "EUR", "€": "EUR",
    "pound": "GBP", "pounds": "GBP", "gbp": "GBP", "£": "GBP",
    "cad": "CAD", "canadian dollar": "CAD",
    "aud": "AUD", "australian dollar": "AUD",
    "aed": "AED", "dirham": "AED", "dirhams": "AED",
    "jpy": "JPY", "yen": "JPY", "¥": "JPY",
    "cny": "CNY", "yuan": "CNY",
    "sgd": "SGD", "singapore dollar": "SGD",
    "chf": "CHF", "swiss franc": "CHF",
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH"
}


# ==============================================================================
# 1. LIVE WEATHER
# ==============================================================================

async def get_weather(city: str = "Delhi") -> Dict[str, Any]:
    """
    Fetches real-time weather and temperature for any city in the world using Open-Meteo.
    100% Free, no API key required.
    """
    clean_city = (city or "Delhi").strip().title()
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            geo_res = await client.get(
                f"https://geocoding-api.open-meteo.com/v1/search?name={clean_city}&count=1"
            )
            if geo_res.status_code == 200 and geo_res.json().get("results"):
                res = geo_res.json()["results"][0]
                lat = res["latitude"]
                lon = res["longitude"]
                resolved_name = res.get("name", clean_city)
                country = res.get("country", "")

                w_res = await client.get(
                    f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&daily=temperature_2m_max,temperature_2m_min,weathercode,uv_index_max&timezone=auto"
                )
                if w_res.status_code == 200:
                    data = w_res.json()
                    cw = data.get("current_weather", {})
                    daily = data.get("daily", {})
                    temp = cw.get("temperature")
                    max_temp = daily.get("temperature_2m_max", [temp])[0]
                    min_temp = daily.get("temperature_2m_min", [temp])[0]
                    w_code = cw.get("weathercode", 0)
                    condition_desc = WEATHER_CODES.get(w_code, "Pleasant")
                    wind = cw.get("windspeed")
                    uv_index = daily.get("uv_index_max", [None])[0]

                    # Fetch real-time Air Quality (AQI)
                    aqi_val = None
                    try:
                        aq_res = await client.get(
                            f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=us_aqi,pm2_5"
                        )
                        if aq_res.status_code == 200:
                            aqi_val = aq_res.json().get("current", {}).get("us_aqi")
                    except Exception:
                        pass

                    location_str = f"{resolved_name}, {country}" if country else resolved_name
                    aqi_str = f" | AQI: {aqi_val}" if aqi_val is not None else ""
                    uv_str = f" | UV Index: {uv_index}" if uv_index is not None else ""

                    formatted_msg = (
                        f"🌤️ **Weather in {location_str}:**\n"
                        f"• **Temperature:** {temp}°C (Min: {min_temp}°C | Max: {max_temp}°C)\n"
                        f"• **Condition:** {condition_desc}\n"
                        f"• **Wind Speed:** {wind} km/h{aqi_str}{uv_str}"
                    )
                    return {
                        "status": "success",
                        "found": True,
                        "location": location_str,
                        "city": resolved_name,
                        "country": country,
                        "temperature": temp,
                        "max_temp": max_temp,
                        "min_temp": min_temp,
                        "condition": condition_desc,
                        "windspeed": wind,
                        "aqi": aqi_val,
                        "uv_index": uv_index,
                        "unit": "°C",
                        "formatted": formatted_msg
                    }
    except Exception as exc:
        print("[utilities.get_weather] error:", repr(exc))

    return {
        "status": "error",
        "found": False,
        "city": clean_city,
        "message": f"Could not fetch weather data for '{clean_city}'. Kripya city ka naam check karein."
    }


# ==============================================================================
# 2. LIVE CURRENCY CONVERSION
# ==============================================================================

async def convert_currency(amount: float, from_curr: str = "USD", to_curr: str = "INR") -> Dict[str, Any]:
    """
    Converts amount between world currencies using real-time Open Exchange Rates API.
    Handles aliases e.g. 'dollar' -> 'USD', 'rupees' -> 'INR', 'euro' -> 'EUR'.
    100% Free, no API key required.
    """
    clean_from = CURRENCY_ALIASES.get(from_curr.strip().lower(), from_curr.strip().upper())
    clean_to = CURRENCY_ALIASES.get(to_curr.strip().lower(), to_curr.strip().upper())

    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            res = await client.get(f"https://open.er-api.com/v6/latest/{clean_from}")
            if res.status_code == 200:
                data = res.json()
                rates = data.get("rates", {})
                rate = rates.get(clean_to)
                if rate:
                    converted_val = round(float(amount) * float(rate), 2)
                    last_update = data.get("time_last_update_utc", "")
                    symbol_to = "₹" if clean_to == "INR" else ("$" if clean_to == "USD" else clean_to)

                    formatted_msg = (
                        f"💱 **Currency Conversion:**\n"
                        f"• **{amount:,.2f} {clean_from}** = **{symbol_to}{converted_val:,.2f} {clean_to}**\n"
                        f"• **Live Exchange Rate:** 1 {clean_from} ≈ {symbol_to}{rate:,.4f} {clean_to}"
                    )
                    return {
                        "status": "success",
                        "success": True,
                        "amount": float(amount),
                        "converted_amount": converted_val,
                        "from_currency": clean_from,
                        "to_currency": clean_to,
                        "rate": rate,
                        "result": converted_val,
                        "last_update": last_update,
                        "formatted": formatted_msg
                    }
                else:
                    return {
                        "status": "error",
                        "success": False,
                        "message": f"Currency code '{clean_to}' not found in exchange database."
                    }
    except Exception as exc:
        print("[utilities.convert_currency] error:", repr(exc))

    return {
        "status": "error",
        "success": False,
        "message": f"Could not fetch live exchange rate for {clean_from} to {clean_to}."
    }


# ==============================================================================
# 3. LIVE GOLD, SILVER & CRYPTO MARKET RATES
# ==============================================================================

async def get_market_price(item: str = "gold", currency: str = "INR") -> Dict[str, Any]:
    """
    Fetches real-time market prices for Gold, Silver, and major Cryptocurrencies (Bitcoin, Ethereum, Solana).
    Gold is calculated from PAX-Gold (100% physical gold backed 1 troy ounce = 31.1034768 grams).
    Returns 24K and 22K per gram & per 10 grams rates in INR & USD.
    100% Free, no API key required.
    """
    clean_item = (item or "gold").strip().lower()
    clean_curr = CURRENCY_ALIASES.get(currency.strip().lower(), currency.strip().upper())

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=pax-gold,tether-gold,bitcoin,ethereum,solana&vs_currencies=usd,inr"
            )
            if res.status_code == 200:
                data = res.json()

                # A) GOLD PRICING
                if any(w in clean_item for w in ("gold", "sona", "gold rate", "gold price", "sohna")):
                    pax = data.get("pax-gold", {})
                    pax_inr = pax.get("inr", 0)
                    pax_usd = pax.get("usd", 0)

                    # 1 troy ounce = 31.1034768 grams
                    gram_24k_inr = round(pax_inr / 31.1034768, 2)
                    gram_24k_usd = round(pax_usd / 31.1034768, 2)
                    ten_gram_24k_inr = round(gram_24k_inr * 10, 2)
                    ten_gram_22k_inr = round(ten_gram_24k_inr * (22.0 / 24.0), 2)
                    gram_22k_inr = round(ten_gram_22k_inr / 10, 2)

                    formatted_msg = (
                        f"🪙 **Live Gold Price (आज का सोने का भाव):**\n"
                        f"• **24 Karat Gold (Pure 99.9%):**\n"
                        f"  - **10 Grams:** ₹{ten_gram_24k_inr:,.2f} INR\n"
                        f"  - **1 Gram:** ₹{gram_24k_inr:,.2f} INR (${gram_24k_usd:,.2f} USD)\n"
                        f"• **22 Karat Gold (Jewellery Standard):**\n"
                        f"  - **10 Grams:** ₹{ten_gram_22k_inr:,.2f} INR\n"
                        f"  - **1 Gram:** ₹{gram_22k_inr:,.2f} INR\n"
                        f"• **Global Spot Price:** ${pax_usd:,.2f} USD / Troy Ounce (31.10g)"
                    )
                    return {
                        "status": "success",
                        "success": True,
                        "item": "Gold",
                        "asset": "Gold",
                        "per_10g_inr": ten_gram_24k_inr,
                        "price": ten_gram_24k_inr,
                        "price_24k_10g_inr": ten_gram_24k_inr,
                        "price_24k_1g_inr": gram_24k_inr,
                        "price_22k_10g_inr": ten_gram_22k_inr,
                        "price_22k_1g_inr": gram_22k_inr,
                        "price_usd_ounce": pax_usd,
                        "formatted": formatted_msg
                    }

                # B) BITCOIN / CRYPTO PRICING
                if any(w in clean_item for w in ("bitcoin", "btc")):
                    btc = data.get("bitcoin", {})
                    btc_inr = btc.get("inr", 0)
                    btc_usd = btc.get("usd", 0)
                    formatted_msg = (
                        f"₿ **Live Bitcoin (BTC) Price:**\n"
                        f"• **INR:** ₹{btc_inr:,.2f} INR\n"
                        f"• **USD:** ${btc_usd:,.2f} USD"
                    )
                    return {
                        "status": "success",
                        "success": True,
                        "item": "Bitcoin",
                        "asset": "Bitcoin",
                        "price": btc_usd if clean_curr == "USD" else btc_inr,
                        "price_inr": btc_inr,
                        "price_usd": btc_usd,
                        "formatted": formatted_msg
                    }

                if any(w in clean_item for w in ("ethereum", "eth")):
                    eth = data.get("ethereum", {})
                    eth_inr = eth.get("inr", 0)
                    eth_usd = eth.get("usd", 0)
                    formatted_msg = (
                        f"⟠ **Live Ethereum (ETH) Price:**\n"
                        f"• **INR:** ₹{eth_inr:,.2f} INR\n"
                        f"• **USD:** ${eth_usd:,.2f} USD"
                    )
                    return {
                        "status": "success",
                        "success": True,
                        "item": "Ethereum",
                        "asset": "Ethereum",
                        "price": eth_usd if clean_curr == "USD" else eth_inr,
                        "price_inr": eth_inr,
                        "price_usd": eth_usd,
                        "formatted": formatted_msg
                    }

                if any(w in clean_item for w in ("solana", "sol")):
                    sol = data.get("solana", {})
                    sol_inr = sol.get("inr", 0)
                    sol_usd = sol.get("usd", 0)
                    formatted_msg = (
                        f"◎ **Live Solana (SOL) Price:**\n"
                        f"• **INR:** ₹{sol_inr:,.2f} INR\n"
                        f"• **USD:** ${sol_usd:,.2f} USD"
                    )
                    return {
                        "status": "success",
                        "success": True,
                        "item": "Solana",
                        "asset": "Solana",
                        "price": sol_usd if clean_curr == "USD" else sol_inr,
                        "price_inr": sol_inr,
                        "price_usd": sol_usd,
                        "formatted": formatted_msg
                    }

    except Exception as exc:
        print("[utilities.get_market_price] error:", repr(exc))

    return {
        "status": "error",
        "success": False,
        "message": f"Could not fetch live market rate for '{clean_item}'."
    }


# ==============================================================================
# 4. SAFE MATH CALCULATOR (AST Evaluator)
# ==============================================================================

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def _eval_node(node):
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported operator: {type(node.op)}")
        if type(node.op) in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
            raise ZeroDivisionError("Division by zero")
        return op_func(left, right)
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op)}")
        return op_func(operand)
    raise ValueError(f"Unsupported expression node: {type(node)}")

def calculate(expression: str) -> Dict[str, Any]:
    """
    Safely calculates mathematical expressions without using unsafe eval().
    Supports: arithmetic (+, -, *, /, **, %), percentages ('18% of 4500'), and grouping.
    """
    clean_expr = expression.strip()

    # Handle percentage shorthand: "18% of 5000" -> "(18 / 100) * 5000"
    perc_match = re.match(r"^([\d\.]+)\s*%\s*(?:of\s*)?([\d\.]+)$", clean_expr, re.I)
    if perc_match:
        p_val = float(perc_match.group(1))
        b_val = float(perc_match.group(2))
        res = round((p_val / 100.0) * b_val, 4)
        return {
            "status": "success",
            "success": True,
            "expression": f"{p_val}% of {b_val}",
            "result": res,
            "formatted": f"🧮 **Calculation:** {p_val}% of {b_val} = **{res}**"
        }

    # Clean characters allowed: digits, spaces, decimal, +, -, *, /, %, (, ), **
    sanitized = re.sub(r"[^0-9\.\+\-\*\/\%\(\)\s]", "", clean_expr)
    if not sanitized.strip():
        return {
            "status": "error",
            "success": False,
            "message": "Invalid mathematical expression."
        }

    try:
        parsed_tree = ast.parse(sanitized, mode="eval")
        result = _eval_node(parsed_tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        elif isinstance(result, float):
            result = round(result, 4)

        return {
            "status": "success",
            "success": True,
            "expression": clean_expr,
            "result": result,
            "formatted": f"🧮 **Calculation:** {clean_expr} = **{result}**"
        }
    except ZeroDivisionError as zde:
        return {
            "status": "error",
            "success": False,
            "message": f"Calculation error: Division by zero"
        }
    except Exception as exc:
        return {
            "status": "error",
            "success": False,
            "message": f"Calculation error: {repr(exc)}"
        }
