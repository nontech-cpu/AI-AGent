"""
Phase 4 Golden Eval Automated Test Suite:
Validates the 65 golden test cases and guarantees benchmark thresholds:
- Recall@5 >= 90%
- Math Accuracy == 100%
- Hallucination Rate == 0%
- Measurements SQL Routing == 100%
"""
import pytest
import os
import json
from evals.golden_benchmark import run_benchmark


def test_phase4_golden_benchmark_thresholds():
    """Runs the golden benchmark harness and verifies all quality & performance thresholds."""
    report = run_benchmark()

    assert report["total_test_cases"] >= 60

    # Recall threshold
    recall_5_val = float(report["recall_at_5"].replace("%", ""))
    assert recall_5_val >= 90.0, f"Expected Recall@5 >= 90%, got {recall_5_val}%"

    # Math precision threshold
    math_val = float(report["math_accuracy"].replace("%", ""))
    assert math_val == 100.0, f"Expected 100% math accuracy, got {math_val}%"

    # Zero hallucination guarantee
    hallucination_val = float(report["hallucination_rate"].replace("%", ""))
    assert hallucination_val == 0.0, f"Expected 0% hallucination rate, got {hallucination_val}%"

    # Structured measurement exact SQL routing
    meas_val = float(report["measurements_routing_accuracy"].replace("%", ""))
    assert meas_val == 100.0, f"Expected 100% measurements routing, got {meas_val}%"

    # Tokens per turn capping
    assert report["avg_tokens_per_turn"] <= 600, f"Token budget exceeded: {report['avg_tokens_per_turn']}"
