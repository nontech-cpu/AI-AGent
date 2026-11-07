# 🚀 PeculiarAI Feature Roadmap: Future Capabilities & Enhancements
> **Inspiration**: Memorae, Instinct AI, Dot, and Inflection Pi  
> **Status**: Ready for Selection & Prioritization  
> **Purpose**: Ye document aane wale high-impact features ka catalog hai. Aap yahan se directly feature select karke bata sakte hain jise hum agle phase mein implement karenge.

---

## 📌 Feature Catalog

### 1. Proactive Life Follow-ups ("Empathetic Care") 🎯
* **Abhi hamara bot kya karta hai:** Jab user message bhejta hai, tabhi bot reply deta hai (purely reactive).
* **Instinct / Memorae kya karte hain:**
  * Agar aapne kal bola tha: *"Bhai kal mera TCS ka interview hai 2 baje"* ya *"aaj meri tabiyat thodi kharab hai"*.
  * Toh agle din specified time ke baad bot khud se Telegram par ek gentle, contextual check-in bhejta hai:
    > *"Bhai interview kaisa raha? Kaisa gaya discussion?"* ya *"Bhai ab tabiyat kaisi hai tumhari?"*
* **Real-World Value:** Bot robotic tool nahi lagta, balki ek real dost jaisa feel hota hai jo aapki life ke milestones aur open loops ka dhyaan rakhta hai.
* **Technical Implementation:**
  * Background cron/scheduler jo unclosed events/milestones ko scan kare.
  * Sentiment & context-aware message generation without spamming (max 1 proactive check-in per day).

---

### 2. Multi-Action Voice Disentanglement (Smart Voice Capture) 🎙️
* **Problem:** Aksar log gaadi chalate waqt, walk karte waqt ya jaldi mein lamba voice note bhejte hain jisme 3-4 alag-alag tasks mix hote hain:
  > *"Yaar ek toh Rahul ko bolna tha kal 4 baje meeting kare, aur sham ko doodh lana hai, aur haan 15 tarikh ko electricity bill ka reminder laga dena."*
* **Instinct Style:** AI akele is ek voice note se 3 structured parallel actions extract kar leta hai:
  1. **Calendar Meeting**: Rahul meeting tomorrow @ 4:00 PM
  2. **To-Do Checklist Item**: Doodh (`shopping_list` / `tasks`)
  3. **Bill Reminder**: Electricity bill on 15th
* **Bot Reply:** Crisp aur direct (1-2 sentences):
  > *"Done bhai! 3 cheezein set kar di hain — Meeting, Doodh checklist, aur Bill reminder 👍"*
* **Technical Implementation:**
  * Whisper transcription ke baad multi-intent schema extraction jo ek turn mein multiple tools pipeline execute kare.

---

### 3. Memory Contradiction & Evolution (Fact Versioning & Temporal Decay) 🧠
* **Problem:** Insaan ki life dynamic hoti hai aur samay ke saath preferences aur reality badalti hai:
  * 2 mahine pehle bola: *"Mera roommate Rahul hai."*
  * Aaj bola: *"Aman naya flatmate aaya hai."*
  * Ya pehle bola: *"I only code in Python"*, ab bola: *"I've completely switched to Rust."*
* **Memorae Style:** Bot puraani memory ko blindly overwrite ya duplicate nahi karta:
  * Puraani memory ko `[Archived / Past State]` mark kar deta hai.
  * Nayi memory ko `[Active / Current State]` promote karta hai.
  * Agar aap kabhi pucho *"mera pehla flatmate kaun tha?"* ➔ Rahul batayega.
  * Agar aap pucho *"abhi roommate kaun hai?"* ➔ Aman batayega.
* **Technical Implementation:**
  * LLM-driven contradiction detector during `save_note_or_fact` jo existing facts ke sath semantic conflict check kare aur `valid_until` timestamp lagaye.

---

### 4. Telegram Interactive Action Buttons (Inline Keyboards) 🔘
* **Abhi:** Jab bot confirm maangta hai: *"Kya main Rahul ko email bhej doon? Haan ya Na bolo"*, user ko manual type karna padta hai.
* **Modern AI Style:** Bot message ke neeche 1-click interactive inline buttons deta hai:
  ```
  [ ✅ Confirm & Send ]    [ ❌ Cancel ]
  ```
  Ya reminder snooze ke liye:
  ```
  [ ⏰ +10 Mins ]    [ 📅 Reschedule ]    [ ✅ Mark Done ]
  ```
* **Fayda:** 1-Click execution! Mobile par typing effort 0 ho jata hai aur user experience 10x fast lagta hai.
* **Technical Implementation:**
  * Telegram `InlineKeyboardMarkup` aur callback query handlers in `main.py`.

---

### 5. Visual "Memory Galaxy" in Companion App 🌌
* **Memorae App UI:** Companion Web App ke andar ek interactive, visually rich 2D/3D bubble graph jahan aap dekh sakte ho ki AI aapke baare mein kya-kya jaanta hai:
  * Category nodes: `Health`, `Work & Code`, `Travel`, `People & Kinship`, `Finance`.
  * Har node par tap karne se uske andar ke memories/facts open hote hain.
  * User directly kisi bhi memory ko 1 click mein edit ya delete kar sakta hai.
* **Technical Implementation:**
  * D3.js ya Three.js/Canvas visualization connected with existing `/api/v1/workspace/park` endpoint.

---

## 📋 Feature Selection Checklist (Aapke Choose Karne Ke Liye)

Neeche diye gaye features me se aap jo bhi feature pehle chahein, bas uska number ya naam bata sakte hain:

- [ ] **Feature 1**: Proactive Life Follow-ups ("Empathetic Care")
- [ ] **Feature 2**: Multi-Action Voice Disentanglement (Smart Voice Capture)
- [ ] **Feature 3**: Memory Contradiction & Evolution (Fact Versioning)
- [ ] **Feature 4**: Telegram Interactive Action Buttons (Inline Keyboards)
- [ ] **Feature 5**: Visual "Memory Galaxy" in Companion App

---
*Created on: 07 November 2026*  
*Project: PeculiarAI Personal Companion Engine*
