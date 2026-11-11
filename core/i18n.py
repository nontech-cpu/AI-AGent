"""
PeculiarAI Internationalization Table (Phase 7e Step 1)
Centralized deterministic text repository keyed by user preferred language (English default).
Used strictly where deterministic system responses are unavoidable (e.g. cancellations, gates).
"""
from typing import Dict, Any

I18N: Dict[str, Dict[str, str]] = {
    "action_cancelled": {
        "english": "Action cancelled. Nothing was scheduled or sent.",
        "hinglish": "Action cancel kar diya gaya hai. Kuch bhi schedule ya send nahi kiya gaya.",
        "hindi": "कार्रवाई रद्द कर दी गई है। कुछ भी शेड्यूल या भेजा नहीं गया।"
    },
    "confirm_meeting": {
        "english": (
            "Please confirm — create this Google Calendar meeting with a Google Meet link?\n"
            "• Calendar: {calendar_name}\n"
            "• Title: {title}\n"
            "• Date: {date_weekday}\n"
            "• Time: {time}\n"
            "• Duration: {duration} minutes\n"
            "• Recurrence: {recurrence}\n"
            "• Attendees: {attendees}\n\n"
            "Reply yes to create or no to cancel."
        ),
        "hinglish": (
            "Please confirm — create this Google Calendar meeting with a Google Meet link?\n"
            "• Calendar: {calendar_name}\n"
            "• Title: {title}\n"
            "• Date: {date_weekday}\n"
            "• Time: {time}\n"
            "• Duration: {duration} minutes\n"
            "• Recurrence: {recurrence}\n"
            "• Attendees: {attendees}\n\n"
            "Reply yes to create or no to cancel."
        ),
        "hindi": (
            "कृपया पुष्टि करें — क्या यह गूगल मीट मीटिंग गूगल कैलेंडर में बनाई जाए?\n"
            "• कैलेंडर: {calendar_name}\n"
            "• शीर्षक: {title}\n"
            "• दिनांक: {date_weekday}\n"
            "• समय: {time}\n"
            "• अवधि: {duration} मिनट\n"
            "• पुनरावृत्ति: {recurrence}\n"
            "• प्रतिभागी: {attendees}\n\n"
            "स्वीकार करने के लिए yes या रद्द करने के लिए no लिखें।"
        )
    },
    "confirm_calendar_event": {
        "english": (
            "Please confirm — add this event to your Google Calendar?\n"
            "• Calendar: {calendar_name}\n"
            "• Title: {title}\n"
            "• Date: {date_weekday}\n"
            "• Time: {time}\n"
            "• Duration: {duration} minutes\n"
            "• Recurrence: {recurrence}\n\n"
            "Reply yes to create or no to cancel."
        ),
        "hinglish": (
            "Please confirm — add this event to your Google Calendar?\n"
            "• Calendar: {calendar_name}\n"
            "• Title: {title}\n"
            "• Date: {date_weekday}\n"
            "• Time: {time}\n"
            "• Duration: {duration} minutes\n"
            "• Recurrence: {recurrence}\n\n"
            "Reply yes to create or no to cancel."
        ),
        "hindi": (
            "कृपया पुष्टि करें — क्या यह इवेंट आपके गूगल कैलेंडर में जोड़ा जाए?\n"
            "• कैलेंडर: {calendar_name}\n"
            "• शीर्षक: {title}\n"
            "• दिनांक: {date_weekday}\n"
            "• समय: {time}\n"
            "• अवधि: {duration} मिनट\n"
            "• पुनरावृत्ति: {recurrence}\n\n"
            "स्वीकार करने के लिए yes या रद्द करने के लिए no लिखें।"
        )
    },
    "confirm_delete_reminder": {
        "english": (
            "Please confirm deletion of the following reminder(s):\n\n"
            "{items_str}\n\n"
            "Reply 'confirm' to delete or 'cancel' to keep them."
        ),
        "hinglish": (
            "Kripya confirmation dein — kya aap ye reminder(s) delete karna chahte hain?\n\n"
            "{items_str}\n\n"
            "Reply karein 'confirm' delete karne ke liye ya 'cancel' cancel karne ke liye."
        )
    },
    "confirm_delete_meeting": {
        "english": (
            "Please confirm — delete '{title}' from your Google Calendar?\n\n"
            "Reply yes to delete or no to cancel."
        ),
        "hinglish": (
            "Please confirm — kya '{title}' ko Google Calendar se delete karna hai?\n\n"
            "Reply karein yes delete karne ke liye ya no cancel karne ke liye."
        )
    },
    "confirm_send_email": {
        "english": (
            "Please confirm 📤 send this email from your connected Gmail?\n\n"
            "• **To:** {to}\n"
            "• **Subject:** {subject}\n"
            "• **Body:** {body}\n\n"
            "Reply 'yes' to send, or let me know what changes to make."
        ),
        "hinglish": (
            "Please confirm 📤 kya ye email aapke Gmail se send kar diya jaye?\n\n"
            "• **To:** {to}\n"
            "• **Subject:** {subject}\n"
            "• **Body:** {body}\n\n"
            "Send karne ke liye 'yes' kahein, ya batayein kya badlav karna hai."
        )
    },
    "confirm_upload_file": {
        "english": (
            "Please confirm — upload this file to Google Drive?\n\n"
            "📄 **Filename:** {filename}\n"
            "📝 **Content:** {content_snippet}\n\n"
            "Reply yes to upload or no to cancel."
        ),
        "hinglish": (
            "Please confirm — kya ye file Google Drive par upload karni hai?\n\n"
            "📄 **Filename:** {filename}\n"
            "📝 **Content:** {content_snippet}\n\n"
            "Upload karne ke liye yes ya cancel karne ke liye no kahein."
        )
    },
    "request_noted_confirm_details": {
        "english": "Your request has been noted. Please confirm the details so I can proceed.",
        "hinglish": "Aapka request note kar liya gaya hai. Kripya details confirm karein taaki main ise proceed kar sakun.",
        "hindi": "आपका अनुरोध दर्ज कर लिया गया है। कृपया विवरण की पुष्टि करें ताकि मैं आगे बढ़ सकूँ।"
    },
    "file_delivered": {
        "english": "✅ I have generated and sent your document **{filename}**! Please check.",
        "hinglish": "✅ Maine aapka document **{filename}** generate karke bhej diya hai! Check kar lijiye.",
        "hindi": "✅ मैंने आपका दस्तावेज़ **{filename}** जनरेट करके भेज दिया है! कृपया देखें।"
    },
    "action_failed": {
        "english": "I tried, but could not complete the action. Please check and try again.",
        "hinglish": "Maine koshish ki lekin action complete nahi ho paya. Kripya check karein.",
        "hindi": "मैंने कोशिश की लेकिन कार्रवाई पूरी नहीं हो सकी। कृपया पुनः प्रयास करें।"
    },
    "unverified_information": {
        "english": "I don't have verified information about this.",
        "hinglish": "Mere paas iski verified information nahi hai.",
        "hindi": "मेरे पास इसकी सत्यापित जानकारी नहीं है।"
    }
}

def get_i18n_string(key: str, lang: str = "english", **kwargs) -> str:
    lang_clean = (lang or "english").strip().lower()
    entry = I18N.get(key, {})
    tmpl = entry.get(lang_clean) or entry.get("english") or ""
    if kwargs:
        try:
            return tmpl.format(**kwargs)
        except Exception:
            return tmpl
    return tmpl

