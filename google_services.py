"""Minimal Google API service layer for the assistant.
All write operations are called only after explicit user confirmation in main.py.
"""
import re
import base64
from datetime import datetime, timedelta
from email.message import EmailMessage
import httpx
from db import engine, text, get_google_token
from google_auth import decrypt_token, refresh_access_token

GOOGLE = 'https://www.googleapis.com'

async def access_token_for_chat(chat_id: int) -> tuple[str, str]:
    with engine.connect() as conn:
        row = conn.execute(text('SELECT id FROM users WHERE telegram_chat_id=:c'), {'c': chat_id}).first()
    if not row:
        raise RuntimeError('Assistant account not found. Finish setup first.')
    token = get_google_token(int(row[0]))
    if not token or not token.get('refresh_token_enc'):
        raise RuntimeError('Google is not connected yet. Ask me to connect Google first.')
    refreshed = await refresh_access_token(decrypt_token(token['refresh_token_enc']))
    access = refreshed.get('access_token') if refreshed else None
    if not access:
        raise RuntimeError('Google token refresh failed. Please reconnect Google.')
    return access, token.get('email', '')

async def _request(method, url, access, **kwargs):
    headers = kwargs.pop('headers', {})
    headers['Authorization'] = f'Bearer {access}'
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.request(method, url, headers=headers, **kwargs)
    if response.is_error:
        raise RuntimeError(f'Google API returned {response.status_code}: {response.text[:400]}')
    return response.json() if response.content else {}

async def send_email(chat_id, to, subject, body):
    access, account = await access_token_for_chat(chat_id)
    msg = EmailMessage()
    msg['To'] = to
    msg['Subject'] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip('=')
    result = await _request('POST', f'{GOOGLE}/gmail/v1/users/me/messages/send', access, json={'raw': raw})
    return result.get('id', 'sent')

async def list_drive_folders(chat_id):
    access, _ = await access_token_for_chat(chat_id)
    data = await _request('GET', f'{GOOGLE}/drive/v3/files', access, params={
        'q': "mimeType='application/vnd.google-apps.folder' and trashed=false",
        'pageSize': 50, 'fields': 'files(id,name,mimeType)', 'orderBy': 'name'
    })
    return data.get('files', [])

async def upload_text_file(chat_id, filename, content, folder_id=None):
    access, _ = await access_token_for_chat(chat_id)
    metadata = {'name': filename}
    if folder_id:
        metadata['parents'] = [folder_id]
    # multipart upload; metadata JSON and text content
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f'{GOOGLE}/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink',
            headers={'Authorization': f'Bearer {access}'},
            files={
                'metadata': (None, __import__('json').dumps(metadata), 'application/json; charset=UTF-8'),
                'file': (filename, content.encode('utf-8'), 'text/plain'),
            })
    if response.is_error:
        raise RuntimeError(f'Google Drive returned {response.status_code}: {response.text[:400]}')
    return response.json()

async def create_meeting(chat_id, title, start_iso, duration_minutes, attendees=None):
    access, _ = await access_token_for_chat(chat_id)
    start = datetime.fromisoformat(start_iso)
    end = start + timedelta(minutes=int(duration_minutes))
    event = {
        'summary': title,
        'start': {'dateTime': start.isoformat(), 'timeZone': 'Asia/Kolkata'},
        'end': {'dateTime': end.isoformat(), 'timeZone': 'Asia/Kolkata'},
        'conferenceData': {'createRequest': {'requestId': __import__('secrets').token_urlsafe(12),
                                             'conferenceSolutionKey': {'type': 'hangoutsMeet'}}},
    }
    emails = attendees or []
    if emails:
        event['attendees'] = [{'email': e} for e in emails]
    result = await _request('POST', f'{GOOGLE}/calendar/v3/calendars/primary/events', access,
                            params={'conferenceDataVersion': 1, 'sendUpdates': 'all'}, json=event)
    meet = (result.get('conferenceData') or {}).get('entryPoints') or []
    meet_url = next((x.get('uri') for x in meet if x.get('entryPointType') == 'video'), '')
    return {
        'id': result.get('id'),
        'summary': result.get('summary', title),
        'start': result.get('start') or {'dateTime': start_iso},
        'end': result.get('end') or {},
        'htmlLink': result.get('htmlLink', ''),
        'meet_url': meet_url
    }


async def list_calendar_events(chat_id, time_min_iso=None, time_max_iso=None, query=None, max_results=20):
    access, _ = await access_token_for_chat(chat_id)
    params = {
        'singleEvents': 'true',
        'orderBy': 'startTime',
        'maxResults': max_results,
    }
    if time_min_iso:
        params['timeMin'] = time_min_iso
    if time_max_iso:
        params['timeMax'] = time_max_iso
    if query:
        params['q'] = query

    data = await _request('GET', f'{GOOGLE}/calendar/v3/calendars/primary/events', access, params=params)
    items = data.get('items', [])
    events = []
    for it in items:
        meet = (it.get('conferenceData') or {}).get('entryPoints') or []
        meet_url = next((x.get('uri') for x in meet if x.get('entryPointType') == 'video'), '')
        events.append({
            'id': it.get('id'),
            'summary': it.get('summary', ''),
            'description': it.get('description', ''),
            'start': it.get('start', {}),
            'end': it.get('end', {}),
            'htmlLink': it.get('htmlLink', ''),
            'meet_url': meet_url,
            'attendees': it.get('attendees', []),
            'conferenceData': it.get('conferenceData', {}),
        })
    return events


async def get_calendar_event(chat_id, event_id):
    access, _ = await access_token_for_chat(chat_id)
    return await _request('GET', f'{GOOGLE}/calendar/v3/calendars/primary/events/{event_id}', access)


async def find_calendar_event(chat_id, title_query=None, target_date=None, target_time=None):
    """
    Search authenticated user's calendar for an event matching title and/or date.
    Returns (matched_events, is_ambiguous).
    """
    from datetime import date as d_date
    now = datetime.now()
    if target_date:
        if isinstance(target_date, str):
            target_date = d_date.fromisoformat(target_date)
        time_min = datetime.combine(target_date - timedelta(days=1), datetime.min.time()).isoformat() + "Z"
        time_max = datetime.combine(target_date + timedelta(days=2), datetime.max.time()).isoformat() + "Z"
    else:
        time_min = (now - timedelta(days=7)).isoformat() + "Z"
        time_max = (now + timedelta(days=30)).isoformat() + "Z"

    events = await list_calendar_events(chat_id, time_min_iso=time_min, time_max_iso=time_max, query=title_query)
    if not events and title_query:
        events = await list_calendar_events(chat_id, time_min_iso=time_min, time_max_iso=time_max)

    if not events:
        return [], False

    matches = []
    norm_q = (title_query or '').strip().lower()
    for ev in events:
        summary = (ev.get('summary') or '').strip().lower()
        if not norm_q or norm_q in summary or summary in norm_q:
            matches.append(ev)

    if not matches and title_query:
        q_words = set(norm_q.split())
        for ev in events:
            ev_words = set((ev.get('summary') or '').lower().split())
            if q_words & ev_words:
                matches.append(ev)

    # If target_time is specified (e.g. '17:00' or 5 PM), match events occurring around that time
    if target_time:
        time_matches = []
        target_hour = None
        target_min = 0
        if isinstance(target_time, str):
            clean_t = target_time.strip().lower()
            if "pm" in clean_t or "am" in clean_t:
                # 5 pm or 5:00 pm
                is_pm = "pm" in clean_t
                digits = re.sub(r"[^\d:]", "", clean_t).split(":")
                try:
                    h = int(digits[0])
                    if is_pm and h < 12:
                        h += 12
                    elif not is_pm and h == 12:
                        h = 0
                    target_hour = h
                    if len(digits) > 1:
                        target_min = int(digits[1][:2])
                except ValueError:
                    pass
            else:
                parts = clean_t.split(":")
                try:
                    target_hour = int(parts[0])
                    if len(parts) > 1:
                        target_min = int(parts[1][:2])
                except ValueError:
                    pass
        elif hasattr(target_time, 'hour'):
            target_hour = target_time.hour
            target_min = target_time.minute

        if target_hour is not None:
            candidate_hours = {target_hour}
            if target_hour < 12:
                candidate_hours.add(target_hour + 12)
            elif target_hour >= 12:
                candidate_hours.add(target_hour - 12)
            pool = matches if matches else events
            for ev in pool:
                start_str = ev.get('start', {}).get('dateTime') or ev.get('start', {}).get('date')
                if start_str and "T" in start_str:
                    try:
                        clean_iso = start_str.replace("Z", "+00:00")
                        ev_dt = datetime.fromisoformat(clean_iso)
                        ev_local = ev_dt.astimezone(IST) if ev_dt.tzinfo else ev_dt
                        if ev_local.hour in candidate_hours and abs(ev_local.minute - target_min) <= 30:
                            time_matches.append(ev)
                    except Exception:
                        pass
            if time_matches:
                matches = time_matches

    is_ambiguous = len(matches) > 1
    return matches, is_ambiguous


async def update_meeting(chat_id, event_id, new_start_iso=None, duration_minutes=None, new_title=None, attendees=None):
    """
    Update an existing Google Calendar event by ID.
    Preserves Meet links, attendees, and descriptions unless explicitly modified.
    Verifies that the event ID has updated start/end time before returning success.
    """
    access, _ = await access_token_for_chat(chat_id)
    existing = await get_calendar_event(chat_id, event_id)
    if not existing:
        raise RuntimeError(f"Event with ID '{event_id}' not found.")

    patch_body = {}
    if new_title:
        patch_body['summary'] = new_title

    if new_start_iso:
        start_dt = datetime.fromisoformat(new_start_iso)
        if duration_minutes:
            dur = int(duration_minutes)
        else:
            exist_start = existing.get('start', {}).get('dateTime')
            exist_end = existing.get('end', {}).get('dateTime')
            if exist_start and exist_end:
                dur = int((datetime.fromisoformat(exist_end) - datetime.fromisoformat(exist_start)).total_seconds() / 60)
            else:
                dur = 30
        end_dt = start_dt + timedelta(minutes=dur)
        patch_body['start'] = {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Kolkata'}
        patch_body['end'] = {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Kolkata'}

    if attendees is not None:
        patch_body['attendees'] = [{'email': e} for e in attendees]

    # Preserve existing conferenceData (Google Meet link)
    if 'conferenceData' in existing:
        patch_body['conferenceData'] = existing['conferenceData']

    result = await _request(
        'PATCH',
        f'{GOOGLE}/calendar/v3/calendars/primary/events/{event_id}',
        access,
        params={'conferenceDataVersion': 1, 'sendUpdates': 'all'},
        json=patch_body,
    )

    if result.get('id') != event_id:
        raise RuntimeError(f"Event ID mismatch after update: expected {event_id}, got {result.get('id')}")

    meet = (result.get('conferenceData') or {}).get('entryPoints') or []
    meet_url = next((x.get('uri') for x in meet if x.get('entryPointType') == 'video'), '')
    if not meet_url:
        old_meet = (existing.get('conferenceData') or {}).get('entryPoints') or []
        meet_url = next((x.get('uri') for x in old_meet if x.get('entryPointType') == 'video'), '')

    return {
        'id': result.get('id'),
        'htmlLink': result.get('htmlLink'),
        'meet_url': meet_url,
        'summary': result.get('summary'),
        'start': result.get('start'),
        'end': result.get('end'),
    }


async def delete_calendar_event(chat_id, event_id):
    """
    Delete an existing Google Calendar event by ID after user confirmation.
    """
    access, _ = await access_token_for_chat(chat_id)
    await _request('DELETE', f'{GOOGLE}/calendar/v3/calendars/primary/events/{event_id}', access)
    return {'id': event_id, 'status': 'deleted'}


def _extract_body_from_gmail_payload(payload: dict) -> str:
    """Extract plain text or cleaned HTML body from a Gmail message payload."""
    plain_text = ""
    html_text = ""

    def walk_parts(part):
        nonlocal plain_text, html_text
        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data:
            try:
                padding = "=" * ((4 - len(data) % 4) % 4)
                decoded = base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")
                if mime_type == "text/plain" and not plain_text:
                    plain_text = decoded
                elif mime_type == "text/html" and not html_text:
                    html_text = decoded
            except Exception:
                pass

        for sub_part in part.get("parts", []):
            walk_parts(sub_part)

    walk_parts(payload)
    if plain_text:
        return re.sub(r"\s+", " ", plain_text).strip()
    if html_text:
        html = re.sub(r"(?is)<(style|script).*?</\1>", " ", html_text)
        return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()
    return ""


async def fetch_user_inbox_messages(chat_id: int, max_results: int = 5, query: str = "is:unread") -> list[dict]:
    """Fetch recent messages from the user's connected Gmail inbox."""
    try:
        access, account = await access_token_for_chat(chat_id)
    except Exception as exc:
        return []

    try:
        data = await _request(
            'GET',
            f'{GOOGLE}/gmail/v1/users/me/messages',
            access,
            params={'q': query, 'maxResults': max_results}
        )
    except Exception as exc:
        print(f"fetch_user_inbox_messages list failed for chat {chat_id}:", repr(exc))
        return []

    msg_stubs = data.get('messages', [])
    messages = []
    for stub in msg_stubs:
        m_id = stub.get('id')
        if not m_id:
            continue
        try:
            full = await _request(
                'GET',
                f'{GOOGLE}/gmail/v1/users/me/messages/{m_id}',
                access,
                params={'format': 'full'}
            )
            payload = full.get('payload', {})
            headers = {h.get('name', '').lower(): h.get('value', '') for h in payload.get('headers', [])}
            subject = headers.get('subject', '')
            sender = headers.get('from', '')
            date_str = headers.get('date', '')
            to_str = headers.get('to', '')
            snippet = full.get('snippet', '')
            body = _extract_body_from_gmail_payload(payload) or snippet

            # Check for attachments (PDFs, images, tickets, invoices)
            attachment_texts = []

            async def extract_attachments(part):
                fn = part.get('filename')
                att_id = part.get('body', {}).get('attachmentId')
                if fn and att_id:
                    try:
                        att_res = await _request(
                            'GET',
                            f'{GOOGLE}/gmail/v1/users/me/messages/{m_id}/attachments/{att_id}',
                            access
                        )
                        att_b64 = att_res.get('data', '')
                        if att_b64:
                            pad = '=' * ((4 - len(att_b64) % 4) % 4)
                            att_bytes = base64.urlsafe_b64decode(att_b64 + pad)
                            lower_fn = fn.lower()
                            if lower_fn.endswith('.pdf'):
                                from multimodal import extract_text_from_pdf
                                txt = extract_text_from_pdf(att_bytes)
                                if txt:
                                    attachment_texts.append(f"[Attached Document: {fn}]\n{txt}")
                            elif any(lower_fn.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.webp')):
                                from multimodal import analyze_image_with_groq
                                img_txt = await analyze_image_with_groq(att_bytes, caption=f"Email attachment {fn}")
                                if img_txt:
                                    attachment_texts.append(f"[Attached Image: {fn}]\n{img_txt}")
                    except Exception as att_err:
                        print(f"Error fetching attachment {fn} in message {m_id}:", att_err)

                for sub in part.get('parts', []):
                    await extract_attachments(sub)

            await extract_attachments(payload)
            if attachment_texts:
                att_combined = "\n\n".join(attachment_texts)
                body = f"{body}\n\n{att_combined}".strip() if body else att_combined

            messages.append({
                'id': m_id,
                'thread_id': full.get('threadId'),
                'subject': subject,
                'sender': sender,
                'date': date_str,
                'to': to_str,
                'snippet': snippet,
                'body': body[:6000],
            })
        except Exception as exc:
            print(f"fetch_user_inbox_messages get message {m_id} failed:", repr(exc))
            continue

    return messages


