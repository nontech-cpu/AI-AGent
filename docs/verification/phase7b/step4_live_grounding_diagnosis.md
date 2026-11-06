# Step 4: Live Grounding & Hallucination Diagnosis

## 1. Trace Evidence for the Reported Live Turns

### A. Turn 1: "jodhpur m kosne mall h" (Trace ID: 1056 & Trace ID: 1076)
- **Chat ID**: `1758056012`
- **User Message**: `jodhpur m kosne mall h`
- **Tools Called**: `['find_nearby_places']`
- **Tool Result (Redacted / Extracted)**:
  ```json
  {
    "action": "find_nearby_places",
    "status": "success",
    "area": "Jodhpur",
    "city": "Jodhpur",
    "message": "• **THE 10 BEST Jodhpur Shopping Malls (2026) - with Reviews**: More to Explore After Your Shopping Spree · 3. Raju's Fashion Mall · 4. Saleraj Store · 5. Bagtesh Fashion · 6. Ashapurna Mall · 7. Risala. Ganesh Art Emporium..."
  }
  ```
- **Source Domain**: `tripadvisor.in` (scraped via DuckDuckGo in `find_nearby_places`).
- **Was the mall list from model memory or search?**:
  **It came from the search tool result (`tripadvisor.in`).**
  The listicle on TripAdvisor is literally headlined *"THE 10 BEST Jodhpur Shopping Malls"* but lists small clothing boutiques and retail shops ("Raju's Fashion Mall", "Saleraj Store", "Bagtesh Fashion", "Risala"). The LLM repeated these exact names from the tool snippet and noted *"listed in recent guides"*.
- **Why did the verifier pass?**:
  `deterministic_verify` and `audit_claims_with_llm` checked whether the names were present in `evidence_str`. Because `evidence_str` literally contained `"Saleraj Store"`, `"Bagtesh Fashion"`, and `"Raju's Fashion Mall"`, the verifier marked `is_grounded: True`.
  **The flaw**: The tool output was low-quality/polluted (shops masquerading as malls in travel listicles). The agent failed to distinguish standalone shops from proper shopping malls or state that the web search returned mixed retail stores.

---

### B. Turn 2: "ratanada se sardarpura best route" (Trace ID: 1057)
- **Chat ID**: `1758056012`
- **User Message**: `bhy jdhpur m ratanada se sardarpura jana ka sbse best route kya h`
- **Tools Called**: `['search_web']`
- **Tool Result**:
  ```json
  {
    "action": "search_web",
    "status": "success",
    "query": "best route from Ratanada to Sardarpura Jodhpur",
    "count": 5,
    "message": "Found 5 live web search result(s) for 'best route from Ratanada to Sardarpura Jodhpur':\n\n• **Cab from Ratanada to Sardarpura, Jodhpur**\nBook cab or auto from Ratanada..."
  }
  ```
- **Bot Reply**:
  *"1. Route: Aap Ratanada Circle se Circuit House Road hote hue Sardarpura ki taraf ja sakte hain. Ye rasta seedha aur kaafi smooth hai.*
  *2. Time: Traffic ke hisaab se aapko mushkil se 10-15 minute lagenge."*
- **Did the route/time claim have any tool source?**:
  **NO.** Neither "Circuit House Road" nor "10-15 minute" was present anywhere in the `search_web` snippet (which only returned generic cab booking headlines). The LLM hallucinated the specific road and the 10-15 minute travel time entirely from its pre-training weights.
- **Why did it pass the verifier?**:
  In `agent/verifier.py:audit_claims_with_llm()`:
  In the fallback heuristic (and loose LLM check):
  ```python
  has_ext_fact = any(w in low for w in ("mall", "malls", "km", "minute lagenge", ...))
  has_evidence = bool(evidence_str and ("status" in evidence_str or "Tool 1" in evidence_str))
  "sourced_by_tool": has_evidence if (has_action_phrase or has_ext_fact) else True
  ```
  Because `search_web` was called and returned `status: success`, `evidence_str` was non-empty. The verifier coarsely checked that *some* tool was called, without verifying that the *specific route name* and *travel time* appeared in the evidence!
- **Phase 5 Rule Fix**:
  Route, distance, and travel time claims require exact maps data. Because live Google Maps navigation is not integrated, the verifier must block ungrounded navigation/minute claims:
  - If user asks for route/travel time: State honestly that live GPS/traffic navigation is not integrated, and provide a direct Google Maps directions URL (`https://www.google.com/maps/dir/?api=1&origin=Ratanada,+Jodhpur&destination=Sardarpura,+Jodhpur`).

---

### C. Turn 3: "aaj date kya h" (Trace ID: 1065, 1068, 1070, 1072)
- **User Message**: `aaj date kya h?`
- **Bot Reply**: `Wednesday, 5 November 2026`
- **Why was Wednesday output?**:
  5 November 2026 was a **Thursday** (`date(2026, 11, 5).strftime("%A") == "Thursday"`).
  The system prompt supplied `{TODAY_ISO}` ("2026-11-05"), but did not supply an explicit `{WEEKDAY}` anchor. The model attempted to compute the day of the week parametrically for 2026 and suffered an off-by-one arithmetic error, outputting "Wednesday".
- **Fix**:
  Inject `{WEEKDAY}` (`timeutil.now(IST).strftime("%A")`) directly into `agent/prompt.py`, and mandate that all date and weekday responses come strictly from this anchor without model mental calendar math.

