"""
PeculiarAI — Unified Connector Error Formatter
Maps standardized ToolResult.error.type to honest, actionable user-facing messages.
Never uses 'information saved nahi hai' for live connector calls.
"""
from typing import Optional
from connectors.base import ErrorType

def format_connector_error(
    error_type: str,
    connector_name: str = "Service",
    instructions: str = "",
    details: str = "",
    lang: str = "hinglish"
) -> str:
    """
    Produces honest, clear, grounded error explanations without robotic generic canned fallbacks.
    """
    name = connector_name.capitalize()
    is_hi = "hindi" in lang.lower() or "hinglish" in lang.lower()

    if error_type == ErrorType.NOT_CONNECTED.value:
        if is_hi:
            instr = f"\n👉 Connect karne ke steps: {instructions}" if instructions else ""
            return f"{name} abhi connected nahi hai.{instr}"
        else:
            instr = f"\nConnection instructions: {instructions}" if instructions else ""
            return f"{name} is not connected yet.{instr}"

    elif error_type == ErrorType.AUTH_EXPIRED.value:
        if is_hi:
            instr = f"\n👉 Dobara connect karein: {instructions}" if instructions else ""
            return f"{name} ka login/token expire ho gaya hai.{instr}"
        else:
            instr = f"\nPlease reconnect: {instructions}" if instructions else ""
            return f"{name} authorization has expired.{instr}"

    elif error_type == ErrorType.RATE_LIMITED.value:
        if is_hi:
            return f"{name} ne temporary rate limit laga di hai. Kripya 1-2 minute baad dobara try karein."
        else:
            return f"{name} API rate limit reached. Please wait a minute before retrying."

    elif error_type == ErrorType.NOT_FOUND.value:
        item = f" '{details}'" if details else ""
        if is_hi:
            return f"{name} par yeh item{item} nahi mila."
        else:
            return f"Item{item} was not found on {name}."

    elif error_type == ErrorType.EMPTY_RESULT.value:
        if is_hi:
            return f"{name} par koi matching items/results nahi mile."
        else:
            return f"No matching items found on {name}."

    elif error_type == ErrorType.PERMISSION_DENIED.value:
        if is_hi:
            return f"{name} par permission nahi mili. Kripya token permissions check karein."
        else:
            return f"Permission denied on {name}. Check token permissions."

    elif error_type == ErrorType.TIMEOUT.value:
        if is_hi:
            return f"{name} API ka response time out ho gaya. Ek baar dobara try karein."
        else:
            return f"{name} request timed out. Please try again."

    else:  # API_ERROR or generic
        d_str = f": {details}" if details else ""
        if is_hi:
            return f"{name} se baat karte waqt dikkat aayi{d_str}."
        else:
            return f"An error occurred while connecting to {name}{d_str}."
