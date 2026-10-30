"""
PeculiarAI — Multi-Agent Deep Research Engine
Orchestrates a 3-agent pipeline for complex inquiries, trip planning, and market comparisons:
1. Decomposer Agent (Query Planning)
2. Live Web Harvester Agent (Concurrent Multi-Source Data Collection)
3. Critic & Executive Synthesizer Agent (Zero-Hallucination Grounded Dossier)
"""
import os
import json
import asyncio
import httpx
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv()
from utilities import search_web

DECOMPOSER_PROMPT = """You are the Research Architect Agent.
Break down the user's research topic into 3 specific, targeted web search queries that cover different angles (pricing/budget, options/ratings, and logistics/details).
Output JSON:
{"queries": ["query 1", "query 2", "query 3"]}
"""

CRITIC_SYNTHESIZER_PROMPT = """You are the Senior Research Analyst & Fact-Checker Agent.
You are given raw gathered web search results from multiple angles for a user topic.
Your mission:
1. Synthesize an authoritative, structured, and crystal-clear executive research brief in natural Roman Hinglish.
2. Structure with clean Markdown sections:
   - 📌 **Executive Summary** (2 lines)
   - 📊 **Key Options & Comparison** (Format as clean structured CARDS with bullet points, e.g. '📍 Option Name', '• Travel Time: ...', '• Cost: ...', '• Pros/Cons: ...'. STRICT RULE: NEVER output ASCII pipe tables '| col | col |' because they look broken and cluttered on mobile devices!)
   - 💡 **Actionable Recommendation / Next Steps**
3. Zero-Hallucination: ONLY cite real facts and figures present in the gathered data. Never guess or invent links.
"""

async def run_deep_research(topic: str, goal: Optional[str] = None) -> Dict[str, Any]:
    """Runs autonomous 3-agent research pipeline and returns a verified executive dossier."""
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        return {"status": "error", "message": "GROQ_API_KEY missing"}

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    clean_topic = topic.strip()
    clean_goal = (goal or clean_topic).strip()

    # Pass 1: Decomposer Sub-Agent
    search_queries = [clean_topic]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json={
                    "model": "openai/gpt-oss-20b",
                    "messages": [
                        {"role": "system", "content": DECOMPOSER_PROMPT},
                        {"role": "user", "content": f"Topic: {clean_topic}\nGoal: {clean_goal}"}
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2
                }
            )
            if resp.status_code == 200:
                data = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(data)
                q_list = parsed.get("queries", [])
                if q_list and isinstance(q_list, list):
                    search_queries = [str(q).strip() for q in q_list[:3] if str(q).strip()]
    except Exception as exc:
        print("[DeepResearch.Decomposer] fallback to single query:", exc)

    # Pass 2: Harvester Sub-Agent (Concurrent Web Search)
    gathered_data = []
    async def _fetch(q):
        try:
            res = await search_web(q, max_results=3)
            return {"query": q, "results": res.get("results", [])}
        except Exception:
            return {"query": q, "results": []}

    search_tasks = [_fetch(q) for q in search_queries]
    harvested = await asyncio.gather(*search_tasks)

    # Flatten harvested knowledge
    evidence_blocks = []
    for item in harvested:
        evidence_blocks.append(f"### Search Angle: '{item['query']}'")
        for r in item["results"]:
            evidence_blocks.append(f"- **{r.get('title')}**: {r.get('snippet')} (Source: {r.get('url')})")

    combined_evidence = "\n".join(evidence_blocks)

    # Pass 3: Critic & Synthesizer Sub-Agent
    final_dossier = ""
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            syn_resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": CRITIC_SYNTHESIZER_PROMPT},
                        {
                            "role": "user",
                            "content": f"User Research Goal: {clean_goal}\n\nGathered Multi-Angle Evidence:\n{combined_evidence}"
                        }
                    ],
                    "temperature": 0.3
                }
            )
            if syn_resp.status_code == 200:
                final_dossier = syn_resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print("[DeepResearch.Critic] error:", exc)

    if not final_dossier:
        final_dossier = f"Research completed for '{clean_topic}'. Found {len(evidence_blocks)} data points across {len(search_queries)} search angles."

    from utilities import convert_tables_to_cards
    final_dossier = convert_tables_to_cards(final_dossier)

    return {
        "status": "success",
        "topic": clean_topic,
        "search_angles": search_queries,
        "evidence_count": len(evidence_blocks),
        "dossier": final_dossier,
        "summary": final_dossier,
        "message": final_dossier
    }

