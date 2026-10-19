import os
import re
import imaplib
import email
from email.header import decode_header, make_header
from email.utils import parseaddr

MAX_BODY = 3000


def _decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _payload(part):
    raw = part.get_payload(decode=True)
    if raw is None:
        return ""
    return raw.decode(part.get_content_charset() or "utf-8", errors="replace")


def _html_to_text(html):
    html = re.sub(r"(?is)<(style|script).*?</\1>", " ", html)
    return re.sub(r"(?s)<[^>]+>", " ", html)


def _body(msg):
    plain, html = None, None
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain" and plain is None:
            plain = _payload(part)
        elif ctype == "text/html" and html is None:
            html = _payload(part)
    text = plain or _html_to_text(html or "")
    return re.sub(r"\s+", " ", text).strip()[:MAX_BODY]


def fetch_unseen(mark_seen=True):
    """Bot inbox ki unread mails padhta hai. Attachments ignore hote hain."""
    user = os.getenv("BOT_EMAIL")
    password = os.getenv("BOT_EMAIL_APP_PASSWORD")
    if not user or not password:
        return []

    results = []
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(user, password)
        imap.select("INBOX")
        _, data = imap.search(None, "UNSEEN")
        what = "(RFC822)" if mark_seen else "(BODY.PEEK[])"

        for num in data[0].split():
            _, msg_data = imap.fetch(num, what)
            msg = email.message_from_bytes(msg_data[0][1])
            auth_headers = [
                str(h).lower() for h in (msg.get_all("Authentication-Results") or [])
            ]
            sender = parseaddr(msg.get("From", ""))[1].lower()
            domain = sender.split("@")[-1] if "@" in sender else ""

            # Trust Google's receiver-added Authentication-Results header only.
            # Gmail commonly reports alignment using header.from= or header.d=,
            # not always the header.i= form used by the old check.
            trusted_auth = [
                h for h in auth_headers
                if h.strip().startswith("mx.google.com;")
            ]
            auth = " ".join(trusted_auth)
            dmarc_pass = bool(
                re.search(r"\bdmarc=pass\b", auth)
                and (
                    f"header.from={domain}" in auth
                    or f"header.from={domain};" in auth
                )
            )
            dkim_pass = bool(
                re.search(r"\bdkim=pass\b", auth)
                and (
                    f"header.d={domain}" in auth
                    or f"header.i=@{domain}" in auth
                    or f"header.i=*@{domain}" in auth
                )
            )
            authenticated = dmarc_pass or dkim_pass
            print(
                "EMAIL AUTH CHECK:",
                "trusted_google_header=", bool(trusted_auth),
                "dmarc_pass=", dmarc_pass,
                "dkim_pass=", dkim_pass,
                "sender_domain=", domain,
            )

            results.append({
                "sender": sender,
                "subject": _decode(msg.get("Subject")),
                "body": _body(msg),
                "authenticated": authenticated,
                "auth": auth[:300],
            })
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return results
