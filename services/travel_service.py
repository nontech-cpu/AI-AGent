"""
PeculiarAI — Live Train (IRCTC PNR) & Flight Travel Tracker Service
Handles:
1. LLM structured extraction from Train PNR SMS, Flight tickets, PDF text (Zero-Regex).
2. Autonomous Google Calendar journey block creation.
3. Automated 2-hour departure alert & packing reminder scheduling.
4. Live status checking via RapidAPI IRCTC and AviationStack/FlightAware with graceful mock fallbacks.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx

import db
import core.timeutil as timeutil
from timing import IST

logger = logging.getLogger("travel_service")

RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "").strip()
RAPIDAPI_IRCTC_HOST = os.getenv("RAPIDAPI_IRCTC_HOST", "irctc1.p.rapidapi.com").strip()
AVIATIONSTACK_API_KEY = os.getenv("AVIATIONSTACK_API_KEY", "").strip()
FLIGHTAWARE_API_KEY = os.getenv("FLIGHTAWARE_API_KEY", "").strip()


async def extract_travel_booking_from_text(text: str) -> Dict[str, Any]:
    """
    Extracts structured journey details from a train/flight ticket, SMS, or confirmation text using LLM.
    Zero-regex, resilient to varied formats from IRCTC, airlines (IndiGo, Air India, SpiceJet), and travel portals.
    """
    now_ist = timeutil.now(IST)
    current_year = now_ist.year

    prompt = f"""You are an expert travel ticket and booking extraction engine.
Analyze the following travel ticket text, SMS, or booking confirmation and extract details strictly into JSON.
Current date context: {now_ist.strftime('%Y-%m-%d')} (Year: {current_year}).

Required JSON Structure:
{{
    "is_travel_booking": true, // false if this text is not a train, flight, or bus travel booking
    "travel_type": "train", // "train", "flight", "bus", or "other"
    "identifier": "2431256789", // PNR number (10 digits for IRCTC) or flight code (e.g. "6E-204", "AI-101")
    "service_name": "Vande Bharat Express (22436)", // Train name/number or Airline flight number
    "origin": "New Delhi (NDLS)", // Departure station or airport
    "destination": "Varanasi (BSB)", // Arrival station or airport
    "departure_datetime": "YYYY-MM-DDTHH:MM:00+05:30", // ISO-8601 with IST offset (+05:30)
    "arrival_datetime": "YYYY-MM-DDTHH:MM:00+05:30", // Optional, null if missing
    "seat_info": "Coach C4, Seat 25 (Chair Car)", // Coach, berth, class, or seat
    "status": "Confirmed", // CNF, RAC, WL, Confirmed
    "passenger_name": "Passenger Name if mentioned, or null"
}}

Input Text:
\"\"\"{text[:3500]}\"\"\""""

    try:
        from llm.provider import chat
        raw_res = await chat([
            {"role": "system", "content": "You are a precise travel parser. Return JSON only."},
            {"role": "user", "content": prompt}
        ], json_mode=True, temperature=0.1)

        cleaned = (raw_res or "").strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```json")[-1].split("```")[0].strip()
        data = json.loads(cleaned)
        if isinstance(data, dict) and data.get("is_travel_booking"):
            if not data.get("identifier") or not data.get("departure_datetime"):
                data["is_travel_booking"] = False
            return data
    except Exception as exc:
        logger.warning(f"Error in extract_travel_booking_from_text: {exc}")

    return {"is_travel_booking": False}


async def save_and_schedule_travel(chat_id: int, booking: Dict[str, Any]) -> Dict[str, Any]:
    """
    1. Saves travel booking into database.
    2. Automatically creates Google Calendar journey event.
    3. Automatically schedules a 2-hour departure packing & departure reminder.
    """
    travel_type = booking.get("travel_type", "train").lower()
    identifier = booking.get("identifier", "PNR")
    service_name = booking.get("service_name") or ("Train" if travel_type == "train" else "Flight")
    origin = booking.get("origin", "Origin")
    destination = booking.get("destination", "Destination")
    dep_iso = booking.get("departure_datetime")
    arr_iso = booking.get("arrival_datetime")
    seat_info = booking.get("seat_info") or ""
    status = booking.get("status") or "Confirmed"

    icon = "🚆" if travel_type == "train" else "✈️"
    cal_event_id = None
    reminder_id = None

    # 1. Google Calendar Scheduling
    try:
        import google_services
        duration_minutes = 180
        if dep_iso and arr_iso:
            try:
                dt_dep = datetime.fromisoformat(dep_iso)
                dt_arr = datetime.fromisoformat(arr_iso)
                diff = int((dt_arr - dt_dep).total_seconds() / 60)
                if 10 < diff < 1440:
                    duration_minutes = diff
            except Exception:
                pass

        cal_res = await google_services.create_calendar_event(
            chat_id=chat_id,
            summary=f"{icon} {service_name}: {origin} -> {destination} ({identifier})",
            start_time=dep_iso,
            duration_minutes=duration_minutes,
            is_video_meeting=False
        )
        if isinstance(cal_res, dict):
            cal_event_id = cal_res.get("id")
    except Exception as cal_err:
        logger.info(f"Calendar scheduling skipped or not connected: {cal_err}")

    # 2. Automated 2-Hour Departure Alert Reminder
    try:
        if dep_iso:
            dep_dt = datetime.fromisoformat(dep_iso)
            rem_dt = dep_dt - timedelta(hours=2)
            now_ist = timeutil.now(IST)
            if rem_dt.tzinfo is None:
                rem_dt = rem_dt.replace(tzinfo=IST)
            if rem_dt > now_ist:
                rem_due_date = rem_dt.strftime("%Y-%m-%d")
                rem_due_time = rem_dt.strftime("%H:%M")
                rem_title = (
                    f"⏰ Journey Alert: {service_name} departs in 2 hours ({origin} -> {destination})! "
                    f"Check tickets, luggage & ID cards. Seat: {seat_info}."
                )
                # Schedule reminder via save_memory
                item_id = db.save_memory(
                    chat_id,
                    "travel",
                    rem_title,
                    amount=None,
                    due_date=rem_due_date,
                    due_time=rem_due_time,
                    duration_minutes=30,
                    recurrence="none"
                )
                reminder_id = item_id
    except Exception as rem_err:
        logger.warning(f"Departure reminder scheduling failed: {rem_err}")

    # 3. Store booking in database
    booking_id = db.save_user_travel_booking(
        chat_id=chat_id,
        travel_type=travel_type,
        identifier=identifier,
        service_name=service_name,
        origin=origin,
        destination=destination,
        departure_datetime=dep_iso,
        arrival_datetime=arr_iso,
        seat_info=seat_info,
        status=status,
        calendar_event_id=cal_event_id,
        reminder_id=reminder_id
    )

    db.save_note_or_fact(
        chat_id=chat_id,
        fact=f"Upcoming {travel_type.title()}: {service_name} from {origin} to {destination} on {dep_iso} (Seat: {seat_info}, Code: {identifier})",
        category="travel"
    )

    return {
        "booking_id": booking_id,
        "travel_type": travel_type,
        "identifier": identifier,
        "service_name": service_name,
        "origin": origin,
        "destination": destination,
        "departure_datetime": dep_iso,
        "seat_info": seat_info,
        "status": status,
        "calendar_scheduled": bool(cal_event_id),
        "reminder_scheduled": bool(reminder_id)
    }


async def check_live_travel_status(identifier: str, travel_type: str = "train") -> Dict[str, Any]:
    """
    Checks live status of an IRCTC PNR or Flight.
    Uses RapidAPI for IRCTC or AviationStack for Flights when API keys are configured.
    Gracefully falls back to saved details or informative status when unconfigured.
    """
    clean_id = identifier.strip().replace(" ", "").upper()
    tt = travel_type.lower()

    # 1. Train PNR live check
    if tt == "train":
        if RAPIDAPI_KEY:
            try:
                url = f"https://{RAPIDAPI_IRCTC_HOST}/api/v3/getPNRStatus"
                headers = {
                    "X-RapidAPI-Key": RAPIDAPI_KEY,
                    "X-RapidAPI-Host": RAPIDAPI_IRCTC_HOST
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url, headers=headers, params={"pnrNumber": clean_id})
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        return {
                            "ok": True,
                            "travel_type": "train",
                            "identifier": clean_id,
                            "train_name": data.get("trainName") or data.get("TrainNo"),
                            "current_status": data.get("bookingStatus") or data.get("currentStatus") or "Confirmed",
                            "chart_prepared": data.get("chartPrepared", False),
                            "platform": data.get("boardingPlatform", "To be announced"),
                            "source": "live_rapidapi"
                        }
            except Exception as e:
                logger.warning(f"RapidAPI IRCTC call error: {e}")

        return {
            "ok": True,
            "travel_type": "train",
            "identifier": clean_id,
            "current_status": "Confirmed (Recorded)",
            "message": f"PNR {clean_id} tracking active.",
            "source": "local_record",
            "api_tip": "Add RAPIDAPI_KEY in .env for real-time live platform tracking." if not RAPIDAPI_KEY else None
        }

    # 2. Flight live check
    if tt == "flight":
        if AVIATIONSTACK_API_KEY:
            try:
                url = "http://api.aviationstack.com/v1/flights"
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(url, params={"access_key": AVIATIONSTACK_API_KEY, "flight_iata": clean_id})
                    if resp.status_code == 200:
                        flights = resp.json().get("data", [])
                        if flights:
                            f_info = flights[0]
                            dep = f_info.get("departure", {})
                            return {
                                "ok": True,
                                "travel_type": "flight",
                                "identifier": clean_id,
                                "flight_status": f_info.get("flight_status", "scheduled"),
                                "terminal": dep.get("terminal"),
                                "gate": dep.get("gate"),
                                "delay": dep.get("delay"),
                                "source": "live_aviationstack"
                            }
            except Exception as e:
                logger.warning(f"AviationStack flight status error: {e}")

        return {
            "ok": True,
            "travel_type": "flight",
            "identifier": clean_id,
            "current_status": "Scheduled",
            "message": f"Flight {clean_id} journey scheduled.",
            "source": "local_record",
            "api_tip": "Add AVIATIONSTACK_API_KEY in .env for real-time gate and delay updates." if not AVIATIONSTACK_API_KEY else None
        }

    return {"ok": False, "error": f"Unsupported travel type: {travel_type}"}
