import os
import re
import html
import secrets
import sqlite3
import urllib.parse
from datetime import datetime, timedelta

import streamlit as st
import requests
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="PeculiarAI — Your personal AI",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------- Configuration ----------
FASTAPI_BASE_URL = os.getenv("FASTAPI_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")
WHATSAPP_CHAT_URL = os.getenv("WHATSAPP_CHAT_URL", "")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("STREAMLIT_GOOGLE_REDIRECT_URI", "http://localhost:8501")
OAUTH_STATE_DB = os.getenv("PECULIARAI_OAUTH_STATE_DB", "peculiarai_oauth_state.sqlite3")
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.compose",
]

# ---------- Styling ----------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
:root { --bg:#080a13; --panel:#111526; --muted:#9ca5c4; --line:rgba(255,255,255,.11); --violet:#a78bfa; --mint:#83f0c8; }
.stApp { background: radial-gradient(ellipse at 15% 5%, rgba(124,58,237,.19), transparent 36%), radial-gradient(ellipse at 88% 22%, rgba(45,212,191,.10), transparent 31%), var(--bg); color:#f7f7ff; font-family:'DM Sans',sans-serif; }
.block-container { max-width:1160px; padding-top:1.25rem; padding-bottom:4rem; }
#MainMenu, footer, header {visibility:hidden;}
section[data-testid="stSidebar"] {background:#0c1020;}
.brand {font-family:Manrope,sans-serif; font-weight:800; letter-spacing:-1.4px; font-size:1.4rem; color:#fff;}
.brand span {color:var(--violet);}
.eyebrow {text-transform:uppercase; letter-spacing:2.3px; font-size:.73rem; font-weight:700; color:var(--mint);}
.hero-title {font-family:Manrope,sans-serif; font-size:clamp(2.7rem,6vw,5.25rem); font-weight:800; letter-spacing:-.065em; line-height:1.02; margin:.7rem 0 1rem; color:#fff;}
.gradient-text {background:linear-gradient(95deg,#c4b5fd 0%,#a5f3fc 54%,#86efac 100%);-webkit-background-clip:text;background-clip:text;color:transparent;}
.subcopy {font-size:1.08rem; line-height:1.8; color:#b9c0d9; max-width:650px;}
.glass {background:linear-gradient(145deg,rgba(255,255,255,.075),rgba(255,255,255,.025)); border:1px solid var(--line); border-radius:24px; padding:1.35rem 1.45rem; box-shadow:0 18px 70px rgba(0,0,0,.15);}
.feature {background:rgba(255,255,255,.035);border:1px solid var(--line);border-radius:18px;padding:1.05rem;height:100%;}
.feature-icon {font-size:1.65rem;margin-bottom:.45rem;}
.muted {color:var(--muted);font-size:.92rem;line-height:1.6;}
.step-pill {display:inline-block;border:1px solid rgba(167,139,250,.4);background:rgba(167,139,250,.10);color:#ddd6fe;padding:.35rem .7rem;border-radius:999px;font-size:.78rem;font-weight:700;}
div.stButton > button, div.stLinkButton > a {border-radius:13px; min-height:2.9rem; font-weight:700; border:1px solid rgba(167,139,250,.42); transition:all .2s ease;}
div.stButton > button[kind="primary"] {background:linear-gradient(100deg,#8b5cf6,#6366f1); color:white; border:0;}
div.stButton > button:hover, div.stLinkButton > a:hover {border-color:#c4b5fd;transform:translateY(-1px);}
[data-testid="stTextInput"] input, [data-testid="stSelectbox"] div[data-baseweb="select"] {background:#0d1120;color:white;border-color:var(--line);border-radius:12px;}
hr {border-color:var(--line);}
.chat-user {background:linear-gradient(100deg,rgba(139,92,246,.24),rgba(99,102,241,.18));border:1px solid rgba(167,139,250,.25);padding:.85rem 1rem;border-radius:18px 18px 5px 18px;margin:.6rem 0 .6rem 15%;}
.chat-assistant {background:rgba(255,255,255,.055);border:1px solid var(--line);padding:.85rem 1rem;border-radius:18px 18px 18px 5px;margin:.6rem 15% .6rem 0;}
.small-note {font-size:.78rem;color:#9099b9;line-height:1.6;}
</style>
""", unsafe_allow_html=True)

# ---------- Session state ----------
for key, default in {
    "page": "welcome",
    "phone": "",
    "country": "🇮🇳 India (+91)",
    "otp_preview": False,
    "otp_code": "",
    "phone_verified": False,
    "verification_id": None,
    "telegram_verification_url": None,
    "google_email": None,
    "email_link_draft": None,
    "email_link_verified": False,
    "email_link_verified_address": None,
    "email_link_address": "",
    "oauth_state": None,
    "messages": [],
    "consent": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

COUNTRIES = [
    "🇮🇳 India (+91)", "🇺🇸 United States (+1)", "🇬🇧 United Kingdom (+44)",
    "🇨🇦 Canada (+1)", "🇦🇺 Australia (+61)", "🇦🇪 UAE (+971)",
    "🇸🇬 Singapore (+65)", "🇳🇿 New Zealand (+64)", "🇵🇰 Pakistan (+92)",
    "🇧🇩 Bangladesh (+880)", "🇳🇵 Nepal (+977)", "🇱🇰 Sri Lanka (+94)",
    "🇩🇪 Germany (+49)", "🇫🇷 France (+33)", "🇮🇹 Italy (+39)",
    "🇪🇸 Spain (+34)", "🇳🇱 Netherlands (+31)", "🇯🇵 Japan (+81)",
    "🇰🇷 South Korea (+82)", "🇧🇷 Brazil (+55)", "🇿🇦 South Africa (+27)",
    "🇸🇦 Saudi Arabia (+966)", "🇶🇦 Qatar (+974)", "🇮🇩 Indonesia (+62)",
    "🇲🇾 Malaysia (+60)", "🇵🇭 Philippines (+63)", "🇹🇷 Türkiye (+90)",
]


def health_status():
    try:
        r = requests.get(f"{FASTAPI_BASE_URL}/health", timeout=2)
        return r.ok
    except requests.RequestException:
        return False


def init_oauth_state_db():
    """Persist one-time OAuth state across Google's browser redirect.

    Older preview versions used a different table schema. This table stores
    only short-lived OAuth state values, so recreate it if its schema is old.
    """
    with sqlite3.connect(OAUTH_STATE_DB) as conn:
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='oauth_states'"
        ).fetchone()
        if existing:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(oauth_states)").fetchall()
            }
            if not {"state", "created_at"}.issubset(columns):
                conn.execute("DROP TABLE oauth_states")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS oauth_states "
            "(state TEXT PRIMARY KEY, created_at TEXT NOT NULL)"
        )
        conn.commit()


def save_oauth_state(state):
    init_oauth_state_db()
    cutoff = (datetime.utcnow() - timedelta(minutes=15)).isoformat()
    with sqlite3.connect(OAUTH_STATE_DB) as conn:
        conn.execute("DELETE FROM oauth_states WHERE created_at < ?", (cutoff,))
        conn.execute("INSERT INTO oauth_states(state, created_at) VALUES (?, ?)", (state, datetime.utcnow().isoformat()))
        conn.commit()


def consume_oauth_state(state):
    if not state:
        return False
    init_oauth_state_db()
    cutoff = (datetime.utcnow() - timedelta(minutes=15)).isoformat()
    with sqlite3.connect(OAUTH_STATE_DB) as conn:
        row = conn.execute("SELECT state FROM oauth_states WHERE state = ? AND created_at >= ?", (state, cutoff)).fetchone()
        if row:
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
            conn.commit()
            return True
    return False


def build_google_url():
    if not GOOGLE_CLIENT_ID:
        return None
    state = secrets.token_urlsafe(24)
    st.session_state.oauth_state = state
    save_oauth_state(state)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def process_google_callback():
    qp = st.query_params
    code = qp.get("code")
    state = qp.get("state")
    error = qp.get("error")
    if error:
        st.error("Google sign-in was cancelled or denied. You can try again.")
        st.query_params.clear()
        return
    if not code:
        return
    if not state or not consume_oauth_state(state):
        st.error("Google sign-in could not be verified. Please start again.")
        st.query_params.clear()
        return
    if not GOOGLE_CLIENT_SECRET:
        st.error("Google OAuth is not fully configured yet. Add GOOGLE_CLIENT_SECRET to .env.")
        st.query_params.clear()
        return
    try:
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            }, timeout=15,
        )
        token_resp.raise_for_status()
        tokens = token_resp.json()
        user_resp = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=10,
        )
        user_resp.raise_for_status()
        profile = user_resp.json()
        st.session_state.google_email = profile.get("email")
        # Never persist tokens in Streamlit session or logs; production storage belongs in backend.
        st.session_state.google_connected_preview = True
        st.session_state.google_scope_preview = tokens.get("scope", "")
        st.session_state.page = "connect"
        st.query_params.clear()
        st.rerun()
    except Exception:
        st.error("Google sign-in failed. Check OAuth redirect URI and credentials, then retry.")
        st.query_params.clear()


process_google_callback()

# ---------- Public privacy policy page ----------
# Open this page using: https://YOUR-PUBLIC-URL/?page=privacy
if st.query_params.get("page", "") == "privacy":
    st.title("PeculiarAI Privacy Policy")
    st.caption("Effective date: 17 October 2026 · Private beta")
    st.markdown("""
This Privacy Policy explains how PeculiarAI handles information when you use its website and connected services. PeculiarAI is currently in a private beta, and some integrations are still previews.

### Information we may collect
- **Account and profile information:** If you choose Google sign-in, the app requests your Google profile name/email information to identify the account in the sign-in flow.
- **Phone number and verification information:** If you use website verification, the phone number you enter, a verification token, a hashed one-time code, verification status, and Telegram chat identifier may be processed by the verification service. A temporary verification code is delivered through the separate PeculiarAI Telegram verification bot; this is not SMS verification.
- **Email address:** If you use the optional email-link flow, the email address you enter is used to prepare a verification email draft. You must review and send that email yourself.
- **Google permissions:** The current preview requests Google profile/email, Gmail compose, and Google Calendar events permissions. The current website integration is a preview and does not yet securely link Google tokens to your PeculiarAI assistant account. Do not rely on this preview for production email or calendar actions.
- **Messages and technical information:** Information you send to PeculiarAI through messaging or chat features may be processed to generate replies. Basic technical information, such as request and error details, may be processed to operate and troubleshoot the service.

### How information is used
Information is used to provide sign-in and verification, prepare user-requested email drafts, respond to requests, maintain service security, and troubleshoot the beta. We do not claim that every integration is production-ready.

### Google user data
If you authorize Google access, Google processes that authorization under Google's own privacy terms. PeculiarAI requests only the scopes displayed on the Google consent screen. The current website sign-in is a preview; Google tokens are not intentionally persisted by the Streamlit page or written to its logs, and are not yet securely linked to an assistant account. Avoid authorizing an account with sensitive data while testing this preview.

### Sharing and service providers
Information may be processed by service providers needed to run the features you choose, such as Google, Telegram, hosting/tunneling infrastructure, and AI model/API providers used to generate replies. Their handling of information is governed by their own terms and privacy policies. We do not sell personal information.

### Retention and security
Verification records are stored by the verification backend for the operation of the temporary verification flow. Temporary codes are intended to expire after 10 minutes. No online service can guarantee absolute security; do not send passwords, payment-card details, or other secrets to PeculiarAI.

### Your choices
You can choose not to use optional Google, email-link, or verification features. You can stop using the beta at any time. For a request to access or delete information associated with your use, contact the PeculiarAI project administrator through the contact channel provided to you.

### Children
PeculiarAI is not designed for children under 13, and they should not use the service.

### Changes to this policy
This policy may be updated as the beta changes. The effective date above will be revised when material changes are made.

### Contact
For privacy questions or data requests, contact the PeculiarAI project administrator through the official contact channel provided with this project.

---

**Beta notice:** This policy describes the current preview as understood at the date above. Review it against the deployed code, actual data-retention practices, and the project owner's contact details before using it for production or submitting it for Google verification.
""")
    st.markdown("[← Return to PeculiarAI](./)")
    st.stop()

# ---------- Public terms of service page ----------
# Open this page using: https://YOUR-PUBLIC-URL/?page=terms
if st.query_params.get("page", "") == "terms":
    st.title("PeculiarAI Terms of Service")
    st.caption("Effective date: 17 October 2026 · Private beta")
    st.markdown("""
These Terms of Service ("Terms") apply to your access to and use of the PeculiarAI website, assistant, and related integrations (the "Service"). The Service is currently a private beta and may change over time. By using the Service, you agree to these Terms. If you do not agree, do not use it.

### 1. The Service and beta status
PeculiarAI is an AI assistant intended to help with everyday information and tasks. Features may be experimental, incomplete, unavailable, or changed or removed without notice. We may limit or suspend access to maintain or protect the Service.

### 2. AI-generated responses
Responses may be inaccurate, incomplete, outdated, or unsuitable for your situation. You are responsible for checking important information before relying on it. PeculiarAI is not a substitute for professional medical, legal, financial, or other expert advice, and should not be used for emergencies or decisions where an error could cause serious harm.

### 3. Your responsibilities
You agree to use the Service lawfully and responsibly. Do not misuse the Service, attempt to gain unauthorized access, interfere with its operation, submit malicious code, or use it to violate another person's rights or applicable law. Do not submit passwords, payment-card details, or other sensitive secrets into prompts or messages.

### 4. Accounts and verification
You are responsible for the accuracy of information you provide and for keeping your accounts and messaging apps secure. Some verification flows are experimental. In particular, receiving a code through Telegram does not by itself prove ownership of the phone number entered on the website. Do not treat preview verification as a production-grade identity or security check.

### 5. Google and other third-party services
If you connect Google or use Telegram, hosting providers, AI model providers, or other external services, those services may be governed by their own terms and privacy policies. You control whether to grant permissions through third-party authorization screens. The current Google integration is a preview and is not yet securely linked to a PeculiarAI assistant account; do not rely on it for production email or calendar actions. PeculiarAI cannot control third-party services or their availability.

### 6. Your content and permissions
You are responsible for having the rights and permissions needed to submit information or content to the Service. You permit PeculiarAI and the service providers required to operate the features you use to process that content for providing, maintaining, securing, and troubleshooting the Service, subject to the Privacy Policy and applicable law.

### 7. Availability and changes
The Service is provided on an experimental, best-effort basis. We do not guarantee uninterrupted availability, error-free operation, or that any feature will remain available. We may update these Terms as the Service changes; the effective date above will be updated when material changes are made.

### 8. Disclaimers and limitation of liability
To the extent permitted by applicable law, the Service is provided "as is" and "as available", without warranties of any kind. To the extent permitted by law, PeculiarAI and its project operators will not be liable for indirect, incidental, special, consequential, or punitive damages, or for loss of data, profits, or opportunities arising from use of or inability to use the Service. Nothing in these Terms excludes or limits liability that cannot lawfully be excluded or limited.

### 9. Suspension or ending use
You may stop using the Service at any time. Access may be suspended or ended if the Service is discontinued, if these Terms are breached, or where reasonably necessary for security, legal, or operational reasons.

### 10. Contact
For questions about these Terms, contact the PeculiarAI project administrator through the official contact channel provided with this project.

---

**Beta notice:** These Terms are a starting point for the current preview. Review them against the deployed service, actual business/operator details, and applicable law before using them for a public launch or formal compliance submission.
""")
    st.markdown("[← Return to PeculiarAI](./)")
    st.stop()

# ---------- Header ----------
header_cols = st.columns([1.4, 3, 1.2])
with header_cols[0]:
    st.markdown('<div class="brand">✦ Peculiar<span>AI</span></div>', unsafe_allow_html=True)
with header_cols[1]:
    st.markdown('<div style="text-align:center;padding-top:.45rem;color:#929bb8;font-size:.84rem">A little less chaos. A lot more you.</div>', unsafe_allow_html=True)
with header_cols[2]:
    st.markdown('<div style="text-align:right;padding-top:.45rem"><span class="step-pill">PRIVATE BETA</span></div>', unsafe_allow_html=True)
st.markdown("<hr>", unsafe_allow_html=True)

# ---------- Pages ----------
if st.session_state.page == "welcome":
    left, right = st.columns([1.15, .85], gap="large", vertical_alignment="center")
    with left:
        st.markdown('<div class="eyebrow">✦ YOUR DAY, WITH A LITTLE MORE CLARITY</div>', unsafe_allow_html=True)
        st.markdown('<div class="hero-title">Meet the AI<br>that gets <span class="gradient-text">your life.</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="subcopy">A personal assistant that remembers the little things, helps you stay on top of meetings and reminders, and lets you talk naturally — not in commands.</div>', unsafe_allow_html=True)
        st.write("")
        b1, b2 = st.columns([1, 1])
        with b1:
            if st.button("Get started  →", type="primary", use_container_width=True):
                st.session_state.page = "phone"
                st.rerun()
        with b2:
            if st.button("Explore the experience", use_container_width=True):
                st.session_state.page = "features"
                st.rerun()
        st.markdown('<div class="small-note" style="margin-top:1rem">Designed to feel personal. Built to ask before taking important actions.</div>', unsafe_allow_html=True)
    with right:
        st.markdown('''<div class="glass" style="min-height:360px;position:relative;overflow:hidden;">
          <div style="font-size:.8rem;color:#a7f3d0;letter-spacing:1.5px;text-transform:uppercase">A glimpse of your assistant</div>
          <div style="font-family:Manrope;font-size:1.6rem;font-weight:800;margin:1rem 0 .5rem">Good afternoon ✨</div>
          <div class="muted">Here's what I can take off your mind today.</div>
          <div style="margin-top:1.4rem;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);padding:1rem;border-radius:15px"><span style="color:#c4b5fd">◷</span> <b>Meeting with Deepak</b><br><span class="muted">Today · 5:00 PM</span></div>
          <div style="margin-top:.75rem;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);padding:1rem;border-radius:15px"><span style="color:#86efac">✓</span> <b>Remember the important things</b><br><span class="muted">Bills, bookings, plans and follow-ups.</span></div>
          <div style="position:absolute;right:-25px;bottom:-70px;width:180px;height:180px;border-radius:50%;background:radial-gradient(circle,rgba(139,92,246,.4),transparent 70%);filter:blur(10px)"></div>
        </div>''', unsafe_allow_html=True)
    st.write("")
    f1, f2, f3 = st.columns(3)
    for col, icon, title, desc in [
        (f1, "✧", "Talk naturally", "Say what you mean in your own words."),
        (f2, "◷", "Stay on top of things", "Meetings, calendar events and reminders."),
        (f3, "⌁", "You stay in control", "Confirm important actions before they happen."),
    ]:
        with col:
            st.markdown(f'<div class="feature"><div class="feature-icon">{icon}</div><b>{title}</b><div class="muted" style="margin-top:.4rem">{desc}</div></div>', unsafe_allow_html=True)

elif st.session_state.page == "features":
    st.markdown('<div class="eyebrow">MADE FOR REAL LIFE</div><div class="hero-title" style="font-size:3.3rem">Less managing.<br><span class="gradient-text">More living.</span></div>', unsafe_allow_html=True)
    a,b,c = st.columns(3)
    for col, icon, title, desc in [
        (a,"◷","Your calendar","Create meetings and keep track of your day."),
        (b,"✉","Your inbox","Connect email and make everyday follow-ups easier."),
        (c,"✦","Your memory","Remember useful details and upcoming commitments."),
    ]:
        with col: st.markdown(f'<div class="feature"><div class="feature-icon">{icon}</div><b>{title}</b><div class="muted">{desc}</div></div>',unsafe_allow_html=True)
    st.write("")
    if st.button("Let's get started →", type="primary"):
        st.session_state.page = "phone"; st.rerun()
    if st.button("Back to welcome"):
        st.session_state.page = "welcome"; st.rerun()

elif st.session_state.page == "phone":
    st.markdown('<div class="eyebrow">STEP 1 OF 2 · YOUR ACCOUNT</div><div class="hero-title" style="font-size:3.2rem">Let’s make this<br><span class="gradient-text">yours.</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="subcopy">Enter your phone number, then open PeculiarAI on Telegram to receive a temporary verification code. This is not an SMS.</div>', unsafe_allow_html=True)
    with st.container(border=True):
        st.selectbox("Country / region", COUNTRIES, key="country", help="Choose your country code.")
        st.text_input("Phone number", key="phone", placeholder="Enter number without country code")
        st.checkbox("I agree to the privacy notice and understand that my messages may be processed by AI services.", key="consent")
        st.info("Temporary testing: the code is delivered inside your Telegram bot chat. The entered phone number itself is not proven to belong to you by this test flow.")
        if st.button("Get code on Telegram →", type="primary", use_container_width=True, disabled=not (st.session_state.phone.strip() and st.session_state.consent)):
            country = st.session_state.get("country", "")
            dial_match = re.search(r"\((\+\d+)\)", country)
            dial = dial_match.group(1) if dial_match else ""
            phone_digits = re.sub(r"\D", "", st.session_state.phone)
            if not phone_digits or len(phone_digits) < 7:
                st.error("Enter a valid phone number.")
            else:
                full_phone = (dial + phone_digits) if dial and not st.session_state.phone.strip().startswith("+") else st.session_state.phone.strip()
                try:
                    resp = requests.post(f"{FASTAPI_BASE_URL}/web/verification/start", json={"phone": full_phone}, timeout=10)
                    if resp.ok:
                        data = resp.json()
                        st.session_state.verification_id = data["verification_id"]
                        st.session_state.telegram_verification_url = data["telegram_url"]
                        st.session_state.otp_code = ""
                        st.session_state.page = "otp"
                        st.rerun()
                    else:
                        try: detail = resp.json().get("detail", "Could not start verification")
                        except Exception: detail = "Could not start verification"
                        st.error(detail)
                except requests.RequestException:
                    st.error("Could not reach FastAPI. Make sure the backend is running on port 8000.")
    st.caption("Testing flow only. Real SMS verification can be added later using an SMS provider.")
    if st.button("← Back"):
        st.session_state.page = "welcome"; st.rerun()

elif st.session_state.page == "otp":
    st.markdown('<div class="eyebrow">STEP 2 OF 3 · VERIFY VIA TELEGRAM</div><div class="hero-title" style="font-size:3.2rem">Check your<br><span class="gradient-text">Telegram bot.</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="subcopy">Open the PeculiarAI Verification bot link below and press Start. The separate OTP bot will send a six-digit code; enter it here to continue.</div>', unsafe_allow_html=True)
    verify_url = st.session_state.get("telegram_verification_url")
    if verify_url:
        st.link_button("Open PeculiarAI on Telegram ↗", verify_url, type="primary", use_container_width=True)
    st.text_input("6-digit verification code", key="otp_code", max_chars=6, placeholder="Enter code from Telegram")
    if st.button("Verify code →", use_container_width=True, disabled=not (st.session_state.get("verification_id") and re.fullmatch(r"\d{6}", st.session_state.otp_code or ""))):
        try:
            resp = requests.post(f"{FASTAPI_BASE_URL}/web/verification/verify", json={"verification_id": st.session_state.verification_id, "otp": st.session_state.otp_code}, timeout=10)
            if resp.ok and resp.json().get("verified"):
                st.session_state.phone_verified = True
                st.session_state.page = "connect"
                st.success("Telegram verification successful.")
                st.rerun()
            else:
                try: detail = resp.json().get("detail", "Verification failed")
                except Exception: detail = "Verification failed"
                st.error(detail)
        except requests.RequestException:
            st.error("Could not reach FastAPI. Check that the backend is running.")
    st.caption("This verifies access to the Telegram account, not ownership of the phone number entered above. It is a temporary test flow, not SMS OTP.")
    if st.button("← Change phone number"):
        st.session_state.page = "phone"; st.rerun()

elif st.session_state.page in ("connect", "workspace"):
    # After Google OAuth, always return to this connection screen.
    # The website intentionally does not host an in-browser assistant chat.
    st.markdown(
        '<div class="eyebrow">YOUR ASSISTANT IS READY</div>'
        '<div class="hero-title" style="font-size:3.1rem">One last step.<br>'
        '<span class="gradient-text">Start the chat.</span></div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="subcopy">Choose how you want to connect. To talk to PeculiarAI, '
        'open WhatsApp below and send the pre-filled first message. Your conversation '
        'will continue in the messaging app, not on this website.</div>',
        unsafe_allow_html=True,
    )

    google_col, wa_col = st.columns(2, gap="large")
    with google_col:
        st.markdown(
            '<div class="glass"><div style="font-size:2rem">✉️</div>'
            '<h3>Connect Google</h3><div class="muted">Authorize Google access '
            'for the services you choose. Google sign-in returns you to this page. '
            'Nothing is created or sent just by signing in.</div></div>',
            unsafe_allow_html=True,
        )
        google_url = build_google_url()
        if google_url:
            st.link_button(
                "Connect Google ↗", google_url,
                type="primary", use_container_width=True,
            )
        else:
            st.button(
                "Google sign-in not configured",
                disabled=True, use_container_width=True,
            )
            st.caption(
                "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
                "STREAMLIT_GOOGLE_REDIRECT_URI in .env."
            )
        if st.session_state.get("google_email"):
            st.success(f"Google sign-in succeeded for {st.session_state.google_email}.")
        st.caption(
            "Note: this Streamlit Google sign-in is still a preview; Google tokens "
            "are not yet securely linked to your existing assistant account."
        )

    with wa_col:
        st.markdown(
            '<div class="glass"><div style="font-size:2rem">💬</div>'
            '<h3>Telegram</h3><div class="muted">Start your conversation with '
            'PeculiarAI in WhatsApp. A pre-filled message will open for you to send.</div></div>',
            unsafe_allow_html=True,
        )

        if TELEGRAM_BOT_USERNAME:
            tg_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=website_chat"
            st.link_button("Start the chat on Telegram ↗", tg_url, use_container_width=True)
            st.caption("This opens your existing PeculiarAI Telegram bot. Tap Start if Telegram asks; the conversation continues in Telegram, not on this website.")
        else:
            st.button("Start the chat on Telegram", disabled=True, use_container_width=True)
            st.warning("Add TELEGRAM_BOT_USERNAME to .env (without the @ is fine).")

    st.write("")
    st.markdown('<div class="eyebrow">EMAIL INTEGRATION</div><div class="hero-title" style="font-size:2.2rem">Link your <span class="gradient-text">email.</span></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subcopy">Link the email address you use for tickets, bills, bookings and meeting messages. '
        'We will prepare a verification email to the existing PeculiarAI inbox; you only need to press Send.</div>',
        unsafe_allow_html=True,
    )
    def render_email_link_section():
        """Render the email-link UI and poll backend status without rerunning the whole page."""
        draft = st.session_state.get("email_link_draft")

        # Check the real backend status on each fragment rerun. The backend must
        # be running the patched main.py with POST /web/email-link/status.
        if draft and not st.session_state.get("email_link_verified", False):
            verification_id = draft.get("_verification_id")
            email = draft.get("_email") or st.session_state.get("email_link_address", "").strip().lower()
            if verification_id and email:
                try:
                    status_response = requests.post(
                        f"{FASTAPI_BASE_URL}/web/email-link/status",
                        json={"verification_id": verification_id, "email": email},
                        timeout=3,
                    )
                    if status_response.ok and status_response.json().get("linked") is True:
                        st.session_state.email_link_verified = True
                        st.session_state.email_link_verified_address = email
                        draft = None
                    elif not status_response.ok:
                        # Don't interrupt the user for a transient status check;
                        # show a useful message only if they explicitly refresh.
                        pass
                except (requests.RequestException, ValueError):
                    pass

        with st.container(border=True):
            if st.session_state.get("email_link_verified"):
                linked_email = st.session_state.get("email_link_verified_address") or st.session_state.get("email_link_address", "")
                st.success(f"✅ Email {linked_email} linked successfully!")
                st.caption("You can now CC or forward bills, bookings and tickets to the PeculiarAI inbox.")
                return

            st.text_input("Your email address", key="email_link_address", placeholder="you@gmail.com")
            if st.button(
                "Prepare verification email →", type="primary", use_container_width=True,
                disabled=not (st.session_state.get("phone_verified") and st.session_state.email_link_address.strip()),
            ):
                try:
                    email = st.session_state.email_link_address.strip().lower()
                    verification_id = st.session_state.get("verification_id")
                    response = requests.post(
                        f"{FASTAPI_BASE_URL}/web/email-link/start",
                        json={"verification_id": verification_id, "email": email},
                        timeout=15,
                    )
                    if response.ok:
                        draft = response.json()
                        draft["_verification_id"] = verification_id
                        draft["_email"] = email
                        st.session_state.email_link_draft = draft
                        st.session_state.email_link_verified = False
                        st.session_state.email_link_verified_address = None
                        st.rerun()
                    else:
                        try:
                            detail = response.json().get("detail", "Could not prepare email verification")
                        except Exception:
                            detail = "Could not prepare email verification"
                        st.error(detail)
                except requests.RequestException:
                    st.error("Could not reach FastAPI. Make sure the backend is running.")

            draft = st.session_state.get("email_link_draft")
            if draft and not st.session_state.get("email_link_verified", False):
                st.markdown("---")
                st.caption("Ready-to-send email")
                recipient = str(draft.get("to") or draft.get("recipient") or "")
                subject = str(draft.get("subject") or "")
                message_body = str(draft.get("body") or "")
                if not recipient:
                    st.error("Backend did not return the recipient email. Restart the updated FastAPI backend and try again.")
                    return
                st.markdown(f"**To:** {html.escape(recipient)}")
                st.markdown(f"**Subject:** {html.escape(subject)}")
                st.text_area(
                    "Message", value=message_body, height=100, disabled=True,
                    key="email_link_draft_preview",
                )
                compose_params = urllib.parse.urlencode({
                    "view": "cm", "fs": "1", "to": recipient,
                    "su": subject, "body": message_body,
                })
                st.link_button(
                    "✉️ Open Gmail and send verification ↗",
                    "https://mail.google.com/mail/?" + compose_params,
                    type="primary", use_container_width=True,
                )
                st.info("Gmail opens with the message filled in. Press Send there. This page checks verification automatically and will replace this section with ‘Email linked successfully’ once the backend confirms it.")

    # Streamlit fragments support automatic 2-second status polling without
    # re-running the unrelated page sections. Older Streamlit gets a safe fallback.
    if hasattr(st, "fragment"):
        st.fragment(run_every=2)(render_email_link_section)()
    else:
        render_email_link_section()
        if st.session_state.get("email_link_draft") and not st.session_state.get("email_link_verified"):
            st.caption("After sending the email, use the refresh button to check its status.")
            if st.button("Refresh email-link status"):
                st.rerun()

    st.write("")
    st.markdown(
        '<div class="small-note">The website is only the connection page. '
        'PeculiarAI conversations happen in your messaging app.</div>',
        unsafe_allow_html=True,
    )
    if st.button("← Back to welcome"):
        st.session_state.page = "welcome"
        st.rerun()

# ---------- Footer / backend status ----------
st.markdown("<hr>", unsafe_allow_html=True)
status = "Connected" if health_status() else "Backend not reachable"
color = "#86efac" if status == "Connected" else "#fbbf24"
st.markdown(f'<div style="display:flex;justify-content:space-between;gap:1rem;flex-wrap:wrap"><span class="small-note">✦ PeculiarAI · Private beta · You remain in control · <a href="?page=privacy" target="_self">Privacy Policy</a> · <a href="?page=terms" target="_self">Terms of Service</a></span><span class="small-note"><span style="color:{color}">●</span> FastAPI: {status}</span></div>', unsafe_allow_html=True)
