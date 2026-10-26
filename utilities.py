"""
PeculiarAI — Production Real-Time Utilities Engine
Provides 100% free, reliable, no-auth-required live web utilities:
1. Live Weather & Forecasts (Open-Meteo) with automatic city cleaning and context inference
2. Live Currency Exchange & Conversions (Open Exchange Rates) with natural alias handling
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


def clean_city_query(raw_city: Optional[str]) -> str:
    """
    Cleans conversational and messy Hinglish city inputs.
    e.g. 'jodhpur m' -> 'Jodhpur', 'bhy jodhpur m weather' -> 'Jodhpur', 'delhi me' -> 'Delhi'
    """
    if not raw_city:
        return ""
    c = re.sub(r"[^a-zA-Z\s]", " ", raw_city)
    stop = {
        "bhy", "bhai", "bro", "in", "at", "m", "me", "mein", "mai", "ka", "ki", "ke", "ko",
        "se", "pe", "par", "weather", "mausam", "temp", "temperature", "today", "aaj",
        "kaisa", "kya", "hai", "h", "city", "shehar", "plz", "pls", "batao", "dikhao", "check"
    }
    words = [w for w in c.strip().split() if w.lower() not in stop]
    return " ".join(words).strip()


def resolve_inferred_city(chat_id: Optional[int]) -> Optional[str]:
    """
    Infers the user's city from context (active ticket route, saved facts, memory)
    when the user does not specify a city in their query.
    """
    if not chat_id:
        return None
    try:
        # 1. Check active ticket route (e.g. "Jodhpur Jn to Abu Road")
        from state.active_context import get_active_ticket
        tkt = get_active_ticket(chat_id)
        if tkt:
            route = tkt.get("route") or ""
            # e.g. "Jodhpur Jn to Abu Road"
            parts = re.split(r"\s+to\s+|\s*-\s*|\s*,\s*", route, flags=re.I)
            if parts:
                origin = clean_city_query(parts[0])
                if origin:
                    return origin.title()

        # 2. Check saved user facts
        from morning_briefing import detect_user_city
        city = detect_user_city(chat_id)
        if city:
            return city
    except Exception as exc:
        print("[utilities.resolve_inferred_city] error:", repr(exc))
    return None


# ==============================================================================
# 1. LIVE WEATHER
# ==============================================================================

async def get_weather(city: Optional[str] = None, chat_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Fetches real-time weather, temperature, AQI, and UV Index for any city in the world using Open-Meteo.
    Cleans messy inputs ('jodhpur m' -> 'Jodhpur') and auto-infers user's city if omitted.
    100% Free, no API key required.
    """
    cleaned = clean_city_query(city)
    if not cleaned and chat_id:
        cleaned = resolve_inferred_city(chat_id) or ""
    if not cleaned:
        cleaned = "Delhi"

    clean_city = cleaned.strip().title()

    # Multi-candidate geocoding fallback for maximum resilience
    candidates = [clean_city]
    words = clean_city.split()
    if len(words) > 1:
        candidates.append(words[0])
        candidates.append(f"{clean_city}, India")
        candidates.append(f"{words[0]}, India")
    else:
        candidates.append(f"{clean_city}, India")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            geo_data = None
            for cand in candidates:
                geo_res = await client.get(
                    f"https://geocoding-api.open-meteo.com/v1/search?name={cand}&count=1"
                )
                if geo_res.status_code == 200 and geo_res.json().get("results"):
                    geo_data = geo_res.json()["results"][0]
                    break

            if geo_data:
                lat = geo_data["latitude"]
                lon = geo_data["longitude"]
                resolved_name = geo_data.get("name", clean_city)
                country = geo_data.get("country", "")

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

def extract_currency_code(text: str, default: str = "USD") -> str:
    """Intelligently identifies currency code from aliases or symbols."""
    if not text:
        return default
    low = text.lower().strip()
    # Check exact match or word match in aliases
    for alias, code in CURRENCY_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", low) or alias == low:
            return code
    # Clean letters
    clean = re.sub(r"[^a-zA-Z]", "", text).upper()
    return clean[:3] if len(clean) >= 3 else default


async def convert_currency(amount: Any, from_curr: str = "USD", to_curr: Optional[str] = None) -> Dict[str, Any]:
    """
    Converts amount between world currencies using real-time Open Exchange Rates API.
    Handles aliases e.g. 'dollar' -> 'USD', 'rupees' -> 'INR', 'euro' -> 'EUR'.
    Intelligently defaults to_currency to INR (or USD if from is INR).
    100% Free, no API key required.
    """
    # Clean amount
    try:
        if isinstance(amount, str):
            amt_cleaned = re.sub(r"[^\d\.]", "", amount)
            amount = float(amt_cleaned)
        else:
            amount = float(amount)
    except Exception:
        amount = 1.0

    clean_from = extract_currency_code(from_curr, default="USD")

    # If to_curr not specified or same as from:
    if not to_curr or to_curr.strip().lower() in ("none", "null", ""):
        clean_to = "USD" if clean_from == "INR" else "INR"
    else:
        clean_to = extract_currency_code(to_curr, default="INR")
        if clean_to == clean_from:
            clean_to = "USD" if clean_from == "INR" else "INR"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(f"https://open.er-api.com/v6/latest/{clean_from}")
            if res.status_code == 200:
                data = res.json()
                rates = data.get("rates", {})
                rate = rates.get(clean_to)
                if rate:
                    converted_val = round(float(amount) * float(rate), 2)
                    last_update = data.get("time_last_update_utc", "")
                    symbol_to = "₹" if clean_to == "INR" else ("$" if clean_to == "USD" else ("€" if clean_to == "EUR" else clean_to))
                    symbol_from = "₹" if clean_from == "INR" else ("$" if clean_from == "USD" else ("€" if clean_from == "EUR" else clean_from))

                    formatted_msg = (
                        f"💱 **Live Currency Conversion:**\n"
                        f"• **{symbol_from}{amount:,.2f} {clean_from}** = **{symbol_to}{converted_val:,.2f} {clean_to}**\n"
                        f"• **Current Rate:** 1 {clean_from} ≈ {symbol_to}{rate:,.4f} {clean_to}"
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
    Fetches real-time market prices for Gold, Silver, and Cryptocurrencies (Bitcoin, Ethereum, Solana).
    - Gold: PAX-Gold (1 troy ounce = 31.1034768 grams). Returns 24K & 22K per 10g & 1g rates.
    - Silver: Kinesis Silver (1 troy ounce = 31.1034768 grams). Returns 1kg, 10g & 1g rates.
    - Crypto: Bitcoin, Ethereum, Solana live prices.
    100% Free, no API key required.
    """
    clean_item = (item or "gold").strip().lower()
    clean_curr = extract_currency_code(currency or "INR", default="INR")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=pax-gold,tether-gold,kinesis-silver,bitcoin,ethereum,solana&vs_currencies=usd,inr"
            )
            if res.status_code == 200:
                data = res.json()

                # A) GOLD PRICING
                if any(w in clean_item for w in ("gold", "sona", "gold rate", "gold price", "sohna", "24k", "22k")):
                    pax = data.get("pax-gold", {}) or data.get("tether-gold", {})
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

                # B) SILVER PRICING
                if any(w in clean_item for w in ("silver", "chandi", "chandi ka bhav", "silver rate")):
                    sil = data.get("kinesis-silver", {})
                    sil_inr = sil.get("inr", 0)
                    sil_usd = sil.get("usd", 0)

                    gram_sil_inr = round(sil_inr / 31.1034768, 2)
                    gram_sil_usd = round(sil_usd / 31.1034768, 2)
                    kg_sil_inr = round(gram_sil_inr * 1000, 2)
                    ten_gram_sil_inr = round(gram_sil_inr * 10, 2)

                    formatted_msg = (
                        f"🥈 **Live Silver Price (आज का चाँदी का भाव):**\n"
                        f"• **1 Kilogram (1 KG):** ₹{kg_sil_inr:,.2f} INR\n"
                        f"• **10 Grams:** ₹{ten_gram_sil_inr:,.2f} INR\n"
                        f"• **1 Gram:** ₹{gram_sil_inr:,.2f} INR (${gram_sil_usd:,.2f} USD)"
                    )
                    return {
                        "status": "success",
                        "success": True,
                        "item": "Silver",
                        "asset": "Silver",
                        "price_1kg_inr": kg_sil_inr,
                        "price_10g_inr": ten_gram_sil_inr,
                        "price_1g_inr": gram_sil_inr,
                        "price": kg_sil_inr,
                        "formatted": formatted_msg
                    }

                # C) BITCOIN / CRYPTO PRICING
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
    except ZeroDivisionError:
        return {
            "status": "error",
            "success": False,
            "message": "Calculation error: Division by zero"
        }
    except Exception as exc:
        return {
            "status": "error",
            "success": False,
            "message": f"Calculation error: {repr(exc)}"
        }
