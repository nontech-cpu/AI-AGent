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
        "english": "I can't verify this information because I don't have verified sources for it.",
        "hinglish": "Mere paas iski verified information nahi hai.",
        "hindi": "मेरे पास इसकी सत्यापित जानकारी नहीं है।",
        "german": "Ich habe dazu keine verifizierten Informationen."
    },
    "wipe_privacy_warning": {
        "english": (
            "⚠️ **Security & Privacy Warning**:\n\n"
            "Are you sure you want to permanently delete all your saved memories, chat history, notes, reminders, smart lists, and contacts? "
            "This action is irreversible and your data cannot be recovered.\n\n"
            "Reply **yes** to confirm or **no/cancel** to abort."
        ),
        "hinglish": (
            "⚠️ **Security & Privacy Warning**:\n\n"
            "Kya aap sach me apni saari memories, chat history, notes, reminders, smart lists aur saved contacts permanently delete karna chahte hain? "
            "Ye action irreversible hai aur data wapas recover nahi hoga.\n\n"
            "Reply **yes/haan** to confirm ya **no/cancel** to abort."
        ),
        "hindi": (
            "⚠️ **सुरक्षा और गोपनीयता चेतावनी**:\n\n"
            "क्या आप वाकई अपनी सभी यादें, चैट इतिहास, नोट्स, रिमाइंडर, स्मार्ट लिस्ट और कॉन्टैक्ट्स स्थायी रूप से हटाना चाहते हैं? "
            "यह प्रक्रिया वापस नहीं की जा सकती।\n\n"
            "पुष्टि करने के लिए **yes** या रद्द करने के लिए **no/cancel** लिखें।"
        ),
        "german": (
            "⚠️ **Sicherheits- & Datenschutzwarnung**:\n\n"
            "Möchten Sie wirklich alle gespeicherten Erinnerungen, den Chatverlauf, Notizen, Listen und Kontakte dauerhaft löschen? "
            "Dieser Vorgang kann nicht rückgängig gemacht werden.\n\n"
            "Antworten Sie mit **yes** zum Bestätigen oder **no/cancel** zum Abbrechen."
        )
    },
    "route_gps_unavailable": {
        "english": (
            "I don't have live GPS navigation or real-time traffic access, "
            "so I cannot predict exact road routes or live travel times. "
            "You can check live traffic and the best route on Google Maps:\n\n"
            "👉 https://www.google.com/maps/dir/?api=1"
        ),
        "hinglish": (
            "Mere paas live GPS navigation aur real-time traffic maps ka live access nahi hai, "
            "isliye main exact travel time ya road route predict nahi kar sakta. "
            "Aap live traffic aur best route ke liye Google Maps check kar sakte hain:\n\n"
            "👉 https://www.google.com/maps/dir/?api=1"
        ),
        "hindi": (
            "मेरे पास लाइव GPS नेविगेशन या रीयल-टाइम ट्रैफिक मैप्स का एक्सेस नहीं है, "
            "इसलिए मैं सटीक सड़क मार्ग या यात्रा समय नहीं बता सकता। "
            "लाइव ट्रैफिक और सही रास्ते के लिए आप Google Maps देख सकते हैं:\n\n"
            "👉 https://www.google.com/maps/dir/?api=1"
        ),
        "german": (
            "Ich habe keinen Zugriff auf Live-GPS-Navigation oder Echtzeit-Verkehrsdaten "
            "und kann daher keine exakte Route oder Fahrzeit vorhersagen. "
            "Live-Verkehr und die beste Route finden Sie auf Google Maps:\n\n"
            "👉 https://www.google.com/maps/dir/?api=1"
        )
    },
    "ticket_booking_unavailable": {
        "english": (
            "I can't book tickets or make purchases directly because I don't have a booking or payment tool. "
            "However, I can help you look up schedules, timings, options, and official booking links!"
        ),
        "hinglish": (
            "Main directly tickets book ya purchases nahi kar sakta kyunki mere paas ticket booking tool nahi hai. "
            "Lekin main schedules, showtimes, options aur direct booking links dhoondhne mein aapki madad kar sakta hoon!"
        ),
        "hindi": (
            "मैं सीधे टिकट बुक या खरीदारी नहीं कर सकता क्योंकि मेरे पास बुकिंग टूल नहीं है। "
            "हालांकि, मैं समय, विकल्प और आधिकारिक बुकिंग लिंक खोजने में आपकी मदद कर सकता हूँ!"
        ),
        "german": (
            "Ich kann keine Tickets direkt buchen oder Zahlungen durchführen, da ich kein Buchungstool habe. "
            "Ich kann Ihnen aber helfen, Fahrpläne, Zeiten und offizielle Buchungslinks zu finden!"
        )
    },
    "confirm_action_prompt": {
        "english": "I've prepared the details. Would you like me to proceed? (Reply yes/no)",
        "hinglish": "Maine details prepare kar li hain. Kya aap confirm karte hain ki ise proceed kar doon? (Reply yes/no)",
        "hindi": "मैंने विवरण तैयार कर लिया है। क्या मैं आगे बढ़ूँ? (yes/no लिखें)",
        "german": "Ich habe die Details vorbereitet. Soll ich fortfahren? (Antworten Sie mit yes/no)"
    },
    "github_verified_repos": {
        "english": "Here are the verified repositories on your GitHub account:\n\n{repos_list}",
        "hinglish": "Aapke GitHub account par ye verified repositories hain:\n\n{repos_list}",
        "hindi": "आपके GitHub खाते में ये सत्यापित रिपॉजिटरी हैं:\n\n{repos_list}",
        "german": "Hier sind die verifizierten Repositories in Ihrem GitHub-Konto:\n\n{repos_list}"
    },
    "malls_verified_fallback": {
        "english": "Verified popular shopping malls in Jodhpur include **Ashapurna Mall** (Shastri Nagar) and **Reliance Smart Mall**.",
        "hinglish": "Jodhpur ke verified malls mein **Ashapurna Mall** (Shastri Nagar) aur **Reliance Smart Mall** popular shopping destinations hain."
    },
    "local_transit_fallback": {
        "english": (
            "Sardarpura to Ratanada is a local city drive of around **3–4 km** (~7–10 minutes via Sardarpura–Ratanada link road).\n\n"
            "🚗 For live traffic and turn-by-turn directions, please check Google Maps:\n"
            "🔗 {maps_link}"
        ),
        "hinglish": (
            "Sardarpura se Ratanada ka faasla sirf **3–4 km** ka local city drive hai (lagbhag 7–10 minute via Sardarpura–Ratanada link road).\n\n"
            "🚗 Live traffic aur exact turn-by-turn route ke liye aap direct Google Maps link use kar sakte hain:\n"
            "🔗 {maps_link}"
        )
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

