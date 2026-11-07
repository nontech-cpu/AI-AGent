# 🛡️ PeculiarAI Project Non-Negotiable Operational Rules

Ye rules user dwara strictly define kiye gaye hain aur aage aane wale kisi bhi feature, update ya bugfix ke liye **100% binding aur mandatory** hain:

---

## 📌 Rule 1: Zero-Deletion & Preservation Policy (Working Code Kabhi Nahi Hilega)
1. **Never Delete or Break Existing Working Logic:**
   - Jo code pehle se sahi kaam kar raha hai (tools, routing, handlers, database models, etc.), usme se koi bhi cheez bina explicit user consent ke **delete, exclude ya disable nahi hogi**.
2. **Pre & Post Verification Check:**
   - Kisi bhi commit ya code update se pehle aur baad mein automated tool parity check run hoga to ensure ki poore **40+ tools** (including `generate_pdf`, `generate_excel`, `create_reminder`, `create_notion_page`, etc.) system mein mapped aur active hain.
3. **Regression Tests Passing Requirement:**
   - Existing test suites (e.g. `test_morning_adaptive_and_safety.py`, `test_privacy_wipe_grounding.py`, `test_agent_v2.py`) must pass 100% before any commit.

---

## 📌 Rule 2: Zero Hardcore Regex Policy (Pure Industry-Standard Architecture)
1. **No Brittle Word-Lists:**
   - Koi bhi feature, routing, ya intent handling hardcoded token lists (`["hi", "ho", "aur bhy kesa h", ...]`) ya raw string regex hacks se nahi banega.
2. **Pure Industry Standard Architecture:**
   - **Intent & Domain Routing:** Pure Semantic Vector Embeddings (Cosine Similarity).
   - **Tool & Data Contracts:** Structured Pydantic / JSON Schemas.
   - **State Continuity:** Structured Active Context JSON Objects (Zero manual guessing).
   - **Safety & Verification:** Structural execution audit contracts & AST / JSON parsing.
   - **Resilience:** Reactive exponential backoff with jitter on HTTP 429/5xx.

---
*Status: Active, Immutable & Enforced*  
*Project: PeculiarAI Personal Companion Engine*
