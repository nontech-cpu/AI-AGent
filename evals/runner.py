"""
PeculiarAI Master Evaluation Runner (Phase 6)
Executes all 12 evaluation scenarios and produces a scorecard.
"""
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
import asyncio
import time
from typing import List, Dict, Any

from evals.scenarios import (
    eval_scenario_01_ananya_typo_and_commitment,
    eval_scenario_02_fake_github_repo_prevention,
    eval_scenario_03_schedule_interval_overlap,
    eval_scenario_04_utc_leakage_prevention,
    eval_scenario_05_multi_turn_cross_question,
    eval_scenario_06_unconfirmed_action_fabrication_prevention,
    eval_scenario_07_missing_data_grounding,
    eval_scenario_08_continuous_memory_extraction,
    eval_scenario_09_entity_alias_resolution,
    eval_scenario_10_trigram_and_fuzzy_retrieval,
    eval_scenario_11_confirmation_execution,
    eval_scenario_12_cancellation,
)

SCENARIOS = [
    ("Scenario 01: Ananya typo & commitment lookup ('bhy annaya ko mujhe ky bhjna th')", eval_scenario_01_ananya_typo_and_commitment),
    ("Scenario 02: Fake GitHub repo hallucination prevention", eval_scenario_02_fake_github_repo_prevention),
    ("Scenario 03: Schedule interval conflict detection (5 PM movie vs 6 PM meeting)", eval_scenario_03_schedule_interval_overlap),
    ("Scenario 04: UTC leakage prevention (12:30 UTC -> 6:00 PM IST)", eval_scenario_04_utc_leakage_prevention),
    ("Scenario 05: Multi-turn cross-question preservation during pending confirmation", eval_scenario_05_multi_turn_cross_question),
    ("Scenario 06: Prevention of fabricated Meet link & unconfirmed meeting claim", eval_scenario_06_unconfirmed_action_fabrication_prevention),
    ("Scenario 07: Grounded response on missing/unknown data", eval_scenario_07_missing_data_grounding),
    ("Scenario 08: Continuous memory extraction & structured storage", eval_scenario_08_continuous_memory_extraction),
    ("Scenario 09: Entity alias deduplication ('annaya' -> 'Ananya Sharma')", eval_scenario_09_entity_alias_resolution),
    ("Scenario 10: Hybrid phonetic and fuzzy memory retrieval", eval_scenario_10_trigram_and_fuzzy_retrieval),
    ("Scenario 11: Explicit confirmation execution ('haan kar do')", eval_scenario_11_confirmation_execution),
    ("Scenario 12: Action cancellation ('nahi mat karo')", eval_scenario_12_cancellation),
]


async def run_all_evaluations() -> bool:
    print("=" * 80, flush=True)
    print("🚀 PECULIARAI MASTER EVALUATION HARNESS — 12 CORE SCENARIOS", flush=True)
    print("=" * 80, flush=True)

    start_time = time.time()
    passed = 0
    failed = 0
    results = []

    for name, func in SCENARIOS:
        t0 = time.time()
        try:
            print(f" [RUN]  {name}...", flush=True)
            res = await func()
            duration = int((time.time() - t0) * 1000)
            if res:
                passed += 1
                results.append((name, "PASSED ✅", f"{duration}ms", None))
                print(f" [PASS] {name} ({duration}ms)", flush=True)
            else:
                failed += 1
                results.append((name, "FAILED ❌", f"{duration}ms", "Scenario returned False"))
                print(f" [FAIL] {name} - Returned False", flush=True)
        except Exception as exc:
            duration = int((time.time() - t0) * 1000)
            failed += 1
            results.append((name, "FAILED ❌", f"{duration}ms", str(exc)))
            print(f" [FAIL] {name} - Exception: {exc}", flush=True)

    total_duration = time.time() - start_time
    print("\n" + "=" * 80, flush=True)
    print(f"EVALUATION SUMMARY: {passed} / {len(SCENARIOS)} PASSED ({(passed / len(SCENARIOS)) * 100:.1f}%) in {total_duration:.2f}s", flush=True)
    print("=" * 80, flush=True)

    if failed > 0:
        print(f"\n⚠️ {failed} scenario(s) failed. Fix issues before production rollout.", flush=True)
        return False
    else:
        print("\n🎉 ALL 12 SCENARIOS PASSED WITH ZERO HALLUCINATIONS AND 100% GROUNDING!", flush=True)
        return True


if __name__ == "__main__":
    success = asyncio.run(run_all_evaluations())
    sys.exit(0 if success else 1)
