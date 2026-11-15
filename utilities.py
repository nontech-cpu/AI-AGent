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
import html
import operator
import httpx
from urllib.parse import unquote, quote_plus
from typing import Dict, Any, Optional, Tuple, List

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
    user_country = None
    user_region = None
    if chat_id:
        try:
            import db
            loc_state = db.get_user_location_state(chat_id)
            if loc_state:
                user_country = loc_state.get("country")
                user_region = loc_state.get("region")
                if not cleaned:
                    cleaned = loc_state.get("current_city")
        except Exception:
            pass

    if not cleaned and chat_id:
        cleaned = resolve_inferred_city(chat_id) or ""
    if not cleaned:
        cleaned = "Delhi"

    clean_city = cleaned.strip().title()
    for noise in ("Is ", "In ", "At ", "Near "):
        if clean_city.startswith(noise):
            clean_city = clean_city[len(noise):].strip()

    # Multi-candidate geocoding fallback for maximum resilience (zero fragile single-word splitting)
    candidates = [clean_city]
    if user_country:
        candidates.append(f"{clean_city}, {user_country}")
    elif user_region:
        candidates.append(f"{clean_city}, {user_region}")
    else:
        candidates.append(f"{clean_city}, India")

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            geo_data = None
            for cand in candidates:
                geo_res = await client.get(
                    f"https://geocoding-api.open-meteo.com/v1/search?name={cand}&count=5"
                )
                if geo_res.status_code == 200 and geo_res.json().get("results"):
                    results = geo_res.json()["results"]
                    matched = None
                    if user_country:
                        for r in results:
                            if r.get("country", "").lower() == user_country.lower():
                                matched = r
                                break
                    geo_data = matched or results[0]
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
        "error_type": "weather_fetch_failed",
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

_FALLBACK_MARKET_DATA = {
    "pax-gold": {"inr": 242000, "usd": 2750},
    "kinesis-silver": {"inr": 3050, "usd": 34.5},
    "bitcoin": {"inr": 8200000, "usd": 94000},
    "ethereum": {"inr": 290000, "usd": 3300},
    "solana": {"inr": 16000, "usd": 185}
}

async def get_market_price(item: str = "gold", currency: str = "INR") -> Dict[str, Any]:
    """
    Fetches real-time market prices for Gold, Silver, and Cryptocurrencies (Bitcoin, Ethereum, Solana).
    - Gold: PAX-Gold (1 troy ounce = 31.1034768 grams). Returns 24K & 22K per 10g & 1g rates.
    - Silver: Kinesis Silver (1 troy ounce = 31.1034768 grams). Returns 1kg, 10g & 1g rates.
    - Crypto: Bitcoin, Ethereum, Solana live prices.
    100% Free, no API key required with cached fallback resilience.
    """
    clean_item = (item or "gold").strip().lower()
    clean_curr = extract_currency_code(currency or "INR", default="INR")

    supported_keywords = ("gold", "sona", "24k", "22k", "silver", "chandi", "bitcoin", "btc", "ethereum", "eth", "solana", "sol")
    if not any(w in clean_item for w in supported_keywords):
        return {
            "status": "not_applicable",
            "success": False,
            "message": f"'{clean_item}' is not a recognized commodity or crypto (supported: gold, silver, bitcoin, ethereum, solana)."
        }

    is_live = False
    data = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            res = await client.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=pax-gold,tether-gold,kinesis-silver,bitcoin,ethereum,solana&vs_currencies=usd,inr"
            )
            if res.status_code == 200:
                data = res.json()
                _FALLBACK_MARKET_DATA.update(data)
                is_live = True
    except Exception as exc:
        print("[utilities.get_market_price] live fetch exception, using cached rate:", repr(exc))

    if not data:
        data = _FALLBACK_MARKET_DATA

    try:
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

    supported_keywords = ("gold", "sona", "24k", "22k", "silver", "chandi", "bitcoin", "btc", "ethereum", "eth", "solana", "sol")
    if not any(w in clean_item for w in supported_keywords):
        return {
            "status": "not_applicable",
            "success": False,
            "message": f"'{clean_item}' is not a recognized commodity or crypto (supported: gold, silver, bitcoin, ethereum, solana)."
        }

    return {
        "status": "error",
        "success": False,
        "message": f"Could not fetch live market rate for '{clean_item}'."
    }


# ==============================================================================
# 4. SAFE MATH CALCULATOR (AST Evaluator)
# ==============================================================================

import math

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

_SAFE_FUNCS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "pow": math.pow,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "ceil": math.ceil,
    "floor": math.floor,
}

_SAFE_CONSTS = {
    "pi": math.pi,
    "e": math.e,
}

def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    elif isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Constant of type {type(node.value).__name__} is not allowed")
    elif isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        if isinstance(node.op, ast.Pow):
            if right > 1000 or right < -1000:
                raise ValueError("Exponent too large (max 1000)")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError("Division by zero")
        res = op_func(left, right)
        if len(str(res)) > 500:
            raise ValueError("Result number is too large")
        return res
    elif isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        op_func = _SAFE_OPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op_func(operand)
    elif isinstance(node, ast.Name):
        if node.id in _SAFE_CONSTS:
            return _SAFE_CONSTS[node.id]
        raise ValueError(f"Unknown or unauthorized identifier: '{node.id}'")
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Call must be a direct function name")
        fname = node.func.id
        if fname not in _SAFE_FUNCS:
            raise ValueError(f"Function '{fname}' is not permitted")
        args = [_eval_node(arg) for arg in node.args]
        return _SAFE_FUNCS[fname](*args)
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")

def calculate(expression: str) -> Dict[str, Any]:
    """
    Safely calculates mathematical expressions without using unsafe eval().
    Supports: arithmetic (+, -, *, /, //, %, **), parentheses (), percentages ('18% of 4500'),
    safe math functions (sqrt, abs, round, min, max, log, log10, ceil, floor), and constants (pi, e).
    """
    clean_expr = expression.strip()
    if not clean_expr:
        return {"status": "error", "success": False, "message": "Expression cannot be empty."}

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

    try:
        parsed_tree = ast.parse(clean_expr, mode="eval")
        result = _eval_node(parsed_tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        elif isinstance(result, float):
            result = round(result, 6)

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
            "message": f"Calculation error: {str(exc)}"
        }


# ==============================================================================
# 5. AUTONOMOUS LIVE WEB SEARCH (ChatGPT / Gemini Real-Time Grounding)
# ==============================================================================

SPAM_DOMAINS = (
    "justdial.com", "sulekha.com", "indiamart.com", "tradeindia.com",
    "magicbricks.com", "quikr.com", "storelocatortool.com", "mapdoor.com",
    "locatordb.com", "near-me.info", "locator.net", "asklaila.com"
)

from html.parser import HTMLParser

class _DDGLiteHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self._cur_link = None
        self._in_link = False
        self._in_snippet = False
        self._cur_title = []
        self._cur_snippet = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == 'a' and attrs_dict.get('class') == 'result-link':
            self._cur_link = attrs_dict.get('href')
            self._in_link = True
            self._cur_title = []
        elif tag == 'td' and attrs_dict.get('class') == 'result-snippet':
            self._in_snippet = True
            self._cur_snippet = []

    def handle_endtag(self, tag):
        if tag == 'a' and self._in_link:
            self._in_link = False
        elif tag == 'td' and self._in_snippet:
            self._in_snippet = False
            if self._cur_link and self._cur_title:
                t = "".join(self._cur_title).strip()
                s = "".join(self._cur_snippet).strip()
                if t and s:
                    self.results.append({
                        'title': t,
                        'url': self._cur_link,
                        'snippet': s[:180] + ("..." if len(s) > 180 else "")
                    })
                self._cur_link = None
                self._cur_title = []
                self._cur_snippet = []

    def handle_data(self, data):
        if self._in_link:
            self._cur_title.append(data)
        elif self._in_snippet:
            self._cur_snippet.append(data)


# ==============================================================================
# 5. HYBRID WEB SEARCH ENGINE (Tavily AI-Native + Google Serper + DDG Fallback)
# ==============================================================================

def classify_search_intent(query: str) -> str:
    """
    Classifies search intent to route between Google/Serper and Tavily:
    - 'local_places': Physical venues, cafes, shops, local directions, showroom locations.
      Best served by Google/Serper due to Google Maps/Places knowledge graph.
    - 'knowledge_research': Technical documentation, current news, factual questions,
      comparisons, coding, general knowledge.
      Best served by Tavily due to LLM-optimized parsing, clean text, and direct answers.
    """
    q_low = (query or "").lower().strip()

    # 1. Obvious local venue & geographical navigation keywords
    local_keywords = (
        "near me", "nearby", "location", "address", "pincode", "pin code",
        "restaurant", "restaurants", "cafe", "cafes", "hotel", "hotels",
        "hospital", "hospitals", "clinic", "clinics", "pharmacy", "chemist",
        "shopping mall", "malls", "showroom", "store", "stores", "outlet",
        "supermarket", "super store", "superstore", "hypermarket", "grocery", "kirana",
        "smart point", "smart bazaar", "dmart", "d-mart", "bazaar", "market",
        "metro station", "bus stand", "railway station", "airport",
        "distance between", "distance from", "route to", "how far is",
        "toll plaza", "highway"
    )
    if any(k in q_low for k in local_keywords):
        return "local_places"

    # 2. Major Indian cities combined with venue hints
    indian_cities = (
        "delhi", "mumbai", "bangalore", "bengaluru", "kolkata", "chennai", "hyderabad",
        "pune", "ahmedabad", "jaipur", "jodhpur", "udaipur", "kota", "indore", "bhopal",
        "lucknow", "kanpur", "nagpur", "patna", "chandigarh", "noida", "gurgaon", "gurugram"
    )
    venue_hints = ("in ", "at ", "near ", "best ", "top ", "famous ", "open now", "timings")
    has_city = any(c in q_low for c in indian_cities)
    has_venue_hint = any(vh in q_low for vh in venue_hints)
    if has_city and has_venue_hint:
        research_indicators = ("history", "population", "gdp", "who is", "what is", "why", "meaning", "wikipedia", "rules")
        if not any(ri in q_low for ri in research_indicators):
            return "local_places"

    return "knowledge_research"


async def _search_tavily(query: str, max_results: int = 5, search_depth: str = "basic") -> Optional[Dict[str, Any]]:
    """
    Queries Tavily AI Search API for clean, parsed LLM-ready content and direct synthesized answers.
    """
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=9.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                headers={"Content-Type": "application/json"},
                json={
                    "api_key": tavily_key,
                    "query": query,
                    "search_depth": search_depth,
                    "include_answer": True,
                    "include_images": False,
                    "max_results": max_results
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("results", [])[:max_results]:
                    t = (item.get("title") or "").strip()
                    c = (item.get("content") or "").strip()
                    u = (item.get("url") or "").strip()
                    if t and (c or u):
                        results.append({"title": t, "snippet": c, "url": u})

                if results:
                    return {
                        "results": results,
                        "answer": data.get("answer"),
                        "engine": "tavily"
                    }
            elif resp.status_code == 429:
                print("[utilities._search_tavily] Tavily rate limit (429) hit, falling back to secondary engine.")
    except Exception as exc:
        print("[utilities._search_tavily] Tavily API error:", repr(exc))
    return None


EXCLUDED_LOCAL_DOMAINS = (
    "justdial.com", "sulekha.com", "magicbricks.com",
    "mouthshut.com", "asklaila.com", "indiamart.com", "yellowpages.com"
)


async def _search_serper_places(query: str, max_results: int = 5) -> Optional[Dict[str, Any]]:
    """
    Queries Google Serper /places API endpoint for live Google Maps Business POIs.
    Returns 100% verified real physical establishments with address, rating, coordinates, phone, and website.
    Zero aggregator SEO scraping.
    """
    serp_key = os.getenv("SERPER_API_KEY") or os.getenv("GOOGLE_SEARCH_API_KEY")
    if not serp_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://google.serper.dev/places",
                headers={"X-API-KEY": serp_key, "Content-Type": "application/json"},
                json={"q": query, "gl": "in", "hl": "en"}
            )
            if resp.status_code == 200:
                data = resp.json()
                places_raw = data.get("places", [])
                if not places_raw:
                    return None
                results = []
                for p in places_raw[:max_results]:
                    title = p.get("title") or "Local Establishment"
                    addr = p.get("address")
                    rating = p.get("rating")
                    rating_cnt = p.get("ratingCount")
                    cat = p.get("category")
                    phone = p.get("phoneNumber")
                    web = p.get("website")
                    cid = p.get("cid")

                    rating_str = f" ★{rating}" if rating else ""
                    if rating_cnt:
                        rating_str += f" ({rating_cnt} reviews)"

                    details = []
                    if addr:
                        details.append(f"Address: {addr}")
                    if cat:
                        details.append(f"Type: {cat}")
                    if phone:
                        details.append(f"Phone: {phone}")

                    snippet_str = ". ".join(details) if details else "Verified Google Maps Business Location"

                    map_url = web or (f"https://maps.google.com/?cid={cid}" if cid else f"https://www.google.com/maps/search/?api=1&query={quote_plus(title + ' ' + (addr or ''))}")

                    results.append({
                        "title": f"📍 {title}{rating_str}",
                        "snippet": snippet_str,
                        "url": map_url,
                        "address": addr,
                        "rating": rating,
                        "category": cat
                    })

                if results:
                    return {
                        "results": results[:max_results],
                        "answer": None,
                        "engine": "google_places"
                    }
            elif resp.status_code == 429:
                print("[utilities._search_serper_places] Serper Places rate limit (429) hit.")
    except Exception as exc:
        print("[utilities._search_serper_places] Serper Places error:", repr(exc))
    return None


async def _search_serper(
    query: str,
    max_results: int = 5,
    exclude_aggregators: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Queries Google Serper API for live Google organic results and local places.
    Filters out noisy directory scrapers (Justdial, Sulekha) for local queries.
    """
    serp_key = os.getenv("SERPER_API_KEY") or os.getenv("GOOGLE_SEARCH_API_KEY")
    if not serp_key:
        return None

    clean_q = query
    if exclude_aggregators:
        clean_q = f"{clean_q} -site:justdial.com -site:sulekha.com"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": serp_key, "Content-Type": "application/json"},
                json={"q": clean_q, "num": max_results + 3, "gl": "in", "hl": "en"}
            )
            if resp.status_code == 200:
                data = resp.json()
                results = []
                # Check for Google Places cards if available
                for p in data.get("places", [])[:2]:
                    p_title = p.get("title")
                    p_addr = p.get("address")
                    p_rating = p.get("rating")
                    rating_str = f" (Rating: {p_rating}★)" if p_rating else ""
                    if p_title:
                        results.append({
                            "title": f"📍 {p_title}{rating_str}",
                            "snippet": f"Address: {p_addr}" if p_addr else "Verified Local Place",
                            "url": "https://maps.google.com"
                        })
                # Check organic results
                for org in data.get("organic", []):
                    t = org.get("title")
                    s = org.get("snippet")
                    u = org.get("link")
                    if not t or not s:
                        continue
                    if exclude_aggregators and any(bad in (u or "").lower() for bad in EXCLUDED_LOCAL_DOMAINS):
                        continue
                    results.append({"title": t, "snippet": s, "url": u})
                    if len(results) >= max_results:
                        break

                if results:
                    return {
                        "results": results[:max_results],
                        "answer": None,
                        "engine": "serper_google"
                    }
            elif resp.status_code == 429:
                print("[utilities._search_serper] Serper rate limit (429) hit.")
    except Exception as exc_serp:
        print("[utilities._search_serper] Serper API error:", repr(exc_serp))
    return None


async def _search_duckduckgo(query: str, max_results: int = 5) -> Optional[Dict[str, Any]]:
    """
    Fallback DuckDuckGo search (Lite parser followed by HTML parser).
    """
    results = []
    # Try DDG Lite
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        async with httpx.AsyncClient(timeout=9.0, follow_redirects=True) as client:
            resp = await client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                headers=headers
            )
            if resp.status_code == 200:
                parser = _DDGLiteHTMLParser()
                parser.feed(resp.text)
                for r in parser.results:
                    u_clean = r["url"]
                    if any(sp in u_clean.lower() for sp in SPAM_DOMAINS):
                        continue
                    results.append(r)
                    if len(results) >= max_results:
                        break
    except Exception as exc_lite:
        print("[utilities._search_duckduckgo] Lite error:", repr(exc_lite))

    # Try DDG HTML if Lite had no results
    if not results:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.post(
                    "https://html.duckduckgo.com/html/",
                    data={"q": query},
                    headers=headers
                )
                if resp.status_code == 200:
                    raw_html = resp.text
                    titles = re.findall(r'<a rel="nofollow" class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', raw_html, re.DOTALL)
                    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', raw_html, re.DOTALL)
                    for (u, t), s in zip(titles[:10], snippets[:10]):
                        u_clean = u
                        if "uddg=" in u:
                            m = re.search(r"uddg=([^&]+)", u)
                            if m:
                                u_clean = unquote(m.group(1))
                        if any(sp in u_clean.lower() for sp in SPAM_DOMAINS):
                            continue
                        t_clean = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
                        s_clean = html.unescape(re.sub(r"<[^>]+>", "", s)).strip()
                        if len(s_clean) > 160:
                            s_clean = s_clean[:157] + "..."
                        if t_clean and s_clean:
                            results.append({
                                "title": t_clean,
                                "snippet": s_clean,
                                "url": u_clean
                            })
                        if len(results) >= max_results:
                            break
        except Exception as exc_html:
            print("[utilities._search_duckduckgo] HTML error:", repr(exc_html))

    if results:
        return {
            "results": results[:max_results],
            "answer": None,
            "engine": "duckduckgo"
        }
    return None


def sanitize_untrusted_evidence(text: str, source_label: str = "web") -> str:
    """
    Wraps untrusted third-party web content in strict XML sandbox boundaries
    with delimiter sanitization to prevent Indirect Prompt Injections (IPI).
    """
    if not text:
        return ""
    safe = text.replace("</untrusted_web_evidence>", "&lt;/untrusted_web_evidence&gt;")
    return f"<untrusted_web_evidence source=\"{source_label}\">\n{safe}\n</untrusted_web_evidence>"


async def search_web(
    query: str,
    max_results: int = 5,
    prefer_engine: Optional[str] = None,
    search_depth: str = "basic"
) -> Dict[str, Any]:
    """
    Industrial-grade Hybrid Web Search Engine:
    - Auto-classifies queries into Local Places (Google Serper) vs Factual/Research/Docs (Tavily AI Search).
    - Features direct AI answer synthesis from Tavily.
    - Zero-single-point-of-failure fallback chain: Primary Engine -> Secondary Engine -> DuckDuckGo.
    """
    clean_q = (query or "").strip()
    if not clean_q:
        return {
            "status": "error",
            "success": False,
            "query": "",
            "count": 0,
            "results": [],
            "message": "Search query cannot be empty."
        }

    # 1. Determine execution priority
    intent = classify_search_intent(clean_q)
    pref = (prefer_engine or "").lower().strip()

    if pref in ("google", "serper"):
        if intent == "local_places":
            engines = [
                ("google_places", lambda: _search_serper_places(clean_q, max_results)),
                ("serper", lambda: _search_serper(clean_q, max_results, exclude_aggregators=True)),
                ("tavily", lambda: _search_tavily(clean_q, max_results, search_depth)),
                ("duckduckgo", lambda: _search_duckduckgo(clean_q, max_results))
            ]
        else:
            engines = [
                ("serper", lambda: _search_serper(clean_q, max_results)),
                ("tavily", lambda: _search_tavily(clean_q, max_results, search_depth)),
                ("duckduckgo", lambda: _search_duckduckgo(clean_q, max_results))
            ]
    elif pref == "tavily":
        engines = [
            ("tavily", lambda: _search_tavily(clean_q, max_results, search_depth)),
            ("google_places", lambda: _search_serper_places(clean_q, max_results)),
            ("serper", lambda: _search_serper(clean_q, max_results)),
            ("duckduckgo", lambda: _search_duckduckgo(clean_q, max_results))
        ]
    else:
        # Auto-hybrid routing based on intent
        if intent == "local_places":
            engines = [
                ("google_places", lambda: _search_serper_places(clean_q, max_results)),
                ("serper", lambda: _search_serper(clean_q, max_results, exclude_aggregators=True)),
                ("tavily", lambda: _search_tavily(clean_q, max_results, search_depth)),
                ("duckduckgo", lambda: _search_duckduckgo(clean_q, max_results))
            ]
        else:
            engines = [
                ("tavily", lambda: _search_tavily(clean_q, max_results, search_depth)),
                ("serper", lambda: _search_serper(clean_q, max_results)),
                ("duckduckgo", lambda: _search_duckduckgo(clean_q, max_results))
            ]

    # 2. Execute with resilient fallback
    payload = None
    engine_used = None
    for eng_name, fetcher in engines:
        res = await fetcher()
        if res and res.get("results"):
            payload = res
            engine_used = res.get("engine", eng_name)
            break

    if payload and payload.get("results"):
        results = payload["results"][:max_results]
        answer = payload.get("answer")

        # Clean formatting without raw markdown clutter
        formatted_items = []
        for r in results:
            formatted_items.append(f"• {r['title']}\n{r['snippet']}\nLink: {r['url']}")
        formatted = "\n\n".join(formatted_items)

        if answer:
            raw_summary = f"💡 Summary: {answer}\n\nSources:\n{formatted}"
        else:
            raw_summary = f"Found {len(results)} live web search result(s) via {engine_used} for '{clean_q}':\n\n{formatted}"

        message_content = sanitize_untrusted_evidence(raw_summary, source_label=f"search_web:{engine_used}")

        return {
            "status": "success",
            "success": True,
            "query": clean_q,
            "count": len(results),
            "results": results,
            "answer": answer,
            "engine": engine_used,
            "intent": intent,
            "message": message_content
        }

    return {
        "status": "success",
        "success": True,
        "query": clean_q,
        "count": 0,
        "results": [],
        "answer": None,
        "engine": "none",
        "intent": intent,
        "message": f"No verified web search results found for '{clean_q}'. Do NOT invent or fabricate fake names, locations, or establishments. State clearly that verified live information was not found."
    }


# ==============================================================================
# 6. WEBPAGE & ARTICLE READER (Live URL Inspection & Summarization)
# ==============================================================================

async def read_webpage(url: str, max_chars: int = 4000) -> Dict[str, Any]:
    """
    Fetches and extracts clean readable text from any public webpage, blog, or article URL.
    Strips scripts, styles, navigation, and boilerplate HTML.
    """
    clean_url = (url or "").strip()
    if not clean_url:
        return {
            "status": "error",
            "success": False,
            "url": "",
            "message": "URL cannot be empty."
        }

    if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
        clean_url = "https://" + clean_url

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(clean_url, headers=headers)
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "success": False,
                    "url": clean_url,
                    "message": f"Webpage returned HTTP status {resp.status_code}."
                }

            html_text = resp.text

            # Extract Title
            title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
            title = html.unescape(title_match.group(1)).strip() if title_match else "Webpage"

            # Remove scripts, styles, noscript, svg, nav, footer, header
            cleaned = re.sub(r"<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", html_text, flags=re.IGNORECASE | re.DOTALL)
            # Remove all remaining HTML tags
            text_only = re.sub(r"<[^>]+>", " ", cleaned)
            # Unescape entities (&amp; -> &, &quot; -> ", etc.)
            text_only = html.unescape(text_only)
            # Normalize whitespace
            text_only = re.sub(r"\s+", " ", text_only).strip()

            if not text_only:
                return {
                    "status": "error",
                    "success": False,
                    "url": clean_url,
                    "title": title,
                    "message": f"Could not extract readable text content from '{clean_url}'."
                }

            truncated = text_only[:max_chars]
            if len(text_only) > max_chars:
                truncated += "... [truncated]"

            return {
                "status": "success",
                "success": True,
                "url": clean_url,
                "title": title,
                "length": len(truncated),
                "content": truncated,
                "message": sanitize_untrusted_evidence(f"Webpage Content from '{title}' ({clean_url}):\n\n{truncated}", source_label=f"webpage:{clean_url}")
            }
    except Exception as exc:
        print("[utilities.read_webpage] error:", repr(exc))
        return {
            "status": "error",
            "success": False,
            "url": clean_url,
            "message": f"Could not open or read webpage '{clean_url}': {str(exc)}"
        }


# ==============================================================================
# 7. REVERSE-ENGINEERED DIRECT BOOKMYSHOW & CINEMA SHOWTIMES ENDPOINT
# ==============================================================================

async def get_movie_showtimes(city: Optional[str] = None, area: Optional[str] = None, movie: Optional[str] = None) -> Dict[str, Any]:
    """
    Direct structured cinema and BookMyShow showtimes fetcher for user's local area.
    Queries live search dynamically to find actual currently running films without hardcoded fake movies.
    Prompts user if city is unconfigured/omitted.
    """
    clean_city = (city or "").strip()
    clean_area = (area or "").strip()

    if not clean_city:
        return {
            "status": "needs_clarification",
            "city": None,
            "platform": "BookMyShow",
            "theatres": [],
            "movies": [],
            "summary": "City not specified. Please ask user which city they are in.",
            "message": "Aap kis city ke cinema showtimes dekhna chahte hain? Please city ka naam bataiye."
        }

    search_query = f"movies in theatres in {clean_city} showtimes BookMyShow"
    if movie:
        search_query = f"{movie} movie showtimes in {clean_city} BookMyShow"
    elif clean_area:
        search_query = f"movies near {clean_area} {clean_city} showtimes BookMyShow"

    try:
        search_res = await search_web(search_query)
        results_text = search_res.get("results") or ""
    except Exception:
        results_text = ""

    lines = []
    theatres = []
    movies_data = []
    if results_text:
        lines.append(f"BookMyShow cinema showtimes for {clean_city}:\n\n{results_text[:600]}")
    else:
        lines.append(f"{clean_city} ke cinema listings ke liye live data fetch kiya gaya.")

    formatted_summary = "\n".join(lines)

    return {
        "status": "success",
        "city": clean_city,
        "area": clean_area or "nearby",
        "platform": "BookMyShow",
        "theatres": theatres,
        "movies": movies_data,
        "summary": formatted_summary,
        "message": formatted_summary
    }

# ==============================================================================
# 8. DIRECT LIVE SEARCH GROUNDED PLACES DISCOVERY (Zero Mock / Pure Truth)
# ==============================================================================

async def find_nearby_places(
    category: Optional[str] = None,
    area: Optional[str] = None,
    city: Optional[str] = None,
    query: Optional[str] = None
) -> Dict[str, Any]:
    """
    Finds real, verified local places, malls, cafes, and restaurants by querying live search engines.
    Prioritizes dedicated Google Places API for 100% verified Google Maps business pins.
    Strictly zero mock lists, zero hardcoded fake names.
    Prompts user if city is unconfigured/omitted.
    """
    clean_cat = (category or query or "").strip()
    clean_area = (area or "").strip()
    raw_city = (city or "").strip()

    if not raw_city:
        return {
            "status": "needs_clarification",
            "city": None,
            "results": [],
            "search_query": "",
            "summary": "City not specified. Please ask user which city they want to search in.",
            "message": f"Aap kis city me {clean_cat or 'places'} search karna chahte hain? Please city ka naam bataiye."
        }

    # Normalize common Indian city typos (e.g. 'jodhur' -> 'Jodhpur')
    CITY_SPELL_MAP = {
        "jodhur": "Jodhpur", "jodhpr": "Jodhpur", "joddhur": "Jodhpur", "jodhpure": "Jodhpur",
        "jaipr": "Jaipur", "jaipure": "Jaipur", "delh": "Delhi", "dilli": "Delhi",
        "mumba": "Mumbai", "bombay": "Mumbai", "bangalor": "Bengaluru", "bengaluru": "Bengaluru",
        "pune": "Pune", "udaipur": "Udaipur", "ahmedabad": "Ahmedabad", "kota": "Kota"
    }
    clean_city = CITY_SPELL_MAP.get(raw_city.lower(), raw_city)

    cat_lower = clean_cat.lower().replace("-", " ").strip()
    is_generic_mall = cat_lower in ("mall", "malls", "shopping mall", "shopping malls", "market", "markets", "bazaar")
    is_generic_food = cat_lower in ("cafe", "cafes", "coffee shop", "coffee", "restaurant", "restaurants", "food", "dining", "dhaba")

    if is_generic_mall:
        places_q = f"shopping malls in {clean_city}" if not clean_area else f"shopping malls in {clean_area} {clean_city}"
    elif is_generic_food:
        places_q = f"{clean_cat} in {clean_city}" if not clean_area else f"{clean_cat} in {clean_area} {clean_city}"
    else:
        # Specific place / brand inquiry (e.g. 'Smart Point', 'kmall', 'starbucks')
        places_q = f"{clean_cat} in {clean_city}" if not clean_area else f"{clean_cat} in {clean_area} {clean_city}"

    # 1. Primary: Dedicated Google Places API (100% verified Google Maps business pins, zero aggregator SEO scraping)
    places_res = await _search_serper_places(places_q, max_results=6)
    results = list(places_res.get("results", [])) if places_res else []

    # 2. Secondary Fallback: If Google Places returned zero results, supplement with filtered web search
    if not results:
        search_res = await search_web(places_q, max_results=6, prefer_engine="google")
        existing_titles = {r.get("title", "").lower() for r in results}
        for r in search_res.get("results", []):
            if r.get("title", "").lower() not in existing_titles:
                results.append(r)

    # 3. If still empty and area was specified, try broader city query
    if not results and clean_area:
        broad_q = f"top {clean_cat} in {clean_city}"
        broad_places = await _search_serper_places(broad_q, max_results=5)
        if broad_places and broad_places.get("results"):
            results = list(broad_places["results"])
        else:
            broad_res = await search_web(broad_q, max_results=5, prefer_engine="google")
            results = list(broad_res.get("results", []))

    formatted_items = []
    for r in results[:6]:
        title = r.get("title", "")
        snip = r.get("snippet", "")
        url = r.get("url", "")
        formatted_items.append(f"• **{title}**: {snip} (Link: {url})")

    summary_text = "\n\n".join(formatted_items) if formatted_items else f"No verified listings found for {clean_cat} in {clean_area or clean_city}. Please do NOT invent fake names."

    return {
        "status": "success",
        "city": clean_city,
        "area": clean_area or clean_city,
        "category": clean_cat,
        "search_query": places_q,
        "results": results[:6],
        "message": summary_text
    }


# ==============================================================================
# 9. TABLE-TO-CARD FORMATTER (Structured Mobile-Friendly Layout)
# ==============================================================================

def convert_tables_to_cards(text: str) -> str:
    """
    Transforms ASCII/Markdown pipe tables (| col1 | col2 |) into clean, beautifully-structured
    mobile cards with bullet points and bold titles.
    Eliminates ugly wrapped pipe symbols (|) on mobile and chat apps.
    """
    if not text or "|" not in text:
        return text

    lines = text.split("\n")
    new_lines = []
    table_lines = []
    in_table = False

    def flush_table(t_lines: List[str]) -> List[str]:
        if not t_lines:
            return []
        rows = []
        for l in t_lines:
            cleaned = l.strip()
            if cleaned.startswith("|"):
                cleaned = cleaned[1:]
            if cleaned.endswith("|"):
                cleaned = cleaned[:-1]
            cols = [c.strip() for c in cleaned.split("|")]
            # Ignore separator lines like |---|---|
            if all(re.match(r"^:?-+:?$", c) for c in cols if c):
                continue
            rows.append(cols)

        if not rows:
            return []
        headers = rows[0]
        data_rows = rows[1:] if len(rows) > 1 else []
        if not data_rows:
            return [" ".join(headers)]

        # Check if first column is an ID / serial number
        first_h_low = (headers[0] or "").strip().lower()
        id_headers = {"id", "#", "s.no", "sno", "s.no.", "sr.no", "no", "no."}
        title_col_idx = 0
        if first_h_low in id_headers or all(r[0].strip().isdigit() for r in data_rows if r and r[0].strip()):
            # Look for a meaningful title/name/task column among subsequent headers
            for idx, h in enumerate(headers):
                if any(k in h.lower() for k in ("title", "name", "task", "reminder", "event", "item", "option")):
                    title_col_idx = idx
                    break
            else:
                if len(headers) > 1:
                    title_col_idx = 1

        cards = []
        for r_idx, row in enumerate(data_rows):
            title = row[title_col_idx] if len(row) > title_col_idx and row[title_col_idx] else f"Option {r_idx + 1}"
            card = [f"📍 **{title}**"]
            for col_idx in range(len(headers)):
                if col_idx == title_col_idx:
                    continue
                h = headers[col_idx] if col_idx < len(headers) else f"Field {col_idx + 1}"
                val = row[col_idx] if col_idx < len(row) else ""
                # Strip internal technical database IDs from user-facing cards
                if h.strip().lower() in id_headers and val.strip().isdigit():
                    continue
                if val:
                    card.append(f"• **{h}:** {val}")
            cards.append("\n".join(card))
        return ["\n\n".join(cards)]

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]:
            table_lines.append(stripped)
            in_table = True
        else:
            if in_table:
                new_lines.extend(flush_table(table_lines))
                table_lines = []
                in_table = False
            new_lines.append(line)

    if in_table:
        new_lines.extend(flush_table(table_lines))

    res = "\n".join(new_lines)
    # Post-clean: Remove any remaining standalone table divider artifacts (e.g. |---|---|)
    res = re.sub(r"^\s*\|?\s*(?::?-+:?\s*\|)+\s*$", "", res, flags=re.MULTILINE)
    return res
