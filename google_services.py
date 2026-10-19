"""Minimal Google API service layer for the assistant.
All write operations are called only after explicit user confirmation in main.py.
"""
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
    return {'id': result.get('id'), 'htmlLink': result.get('htmlLink'), 'meet_url': meet_url}
