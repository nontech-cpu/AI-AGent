"""
Pytest integration for Phase 6 evaluation scenarios.
"""
import pytest
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


@pytest.mark.asyncio
async def test_scenario_01():
    assert await eval_scenario_01_ananya_typo_and_commitment() is True


@pytest.mark.asyncio
async def test_scenario_02():
    assert await eval_scenario_02_fake_github_repo_prevention() is True


@pytest.mark.asyncio
async def test_scenario_03():
    assert await eval_scenario_03_schedule_interval_overlap() is True


@pytest.mark.asyncio
async def test_scenario_04():
    assert await eval_scenario_04_utc_leakage_prevention() is True


@pytest.mark.asyncio
async def test_scenario_05():
    assert await eval_scenario_05_multi_turn_cross_question() is True


@pytest.mark.asyncio
async def test_scenario_06():
    assert await eval_scenario_06_unconfirmed_action_fabrication_prevention() is True


@pytest.mark.asyncio
async def test_scenario_07():
    assert await eval_scenario_07_missing_data_grounding() is True


@pytest.mark.asyncio
async def test_scenario_08():
    assert await eval_scenario_08_continuous_memory_extraction() is True


@pytest.mark.asyncio
async def test_scenario_09():
    assert await eval_scenario_09_entity_alias_resolution() is True


@pytest.mark.asyncio
async def test_scenario_10():
    assert await eval_scenario_10_trigram_and_fuzzy_retrieval() is True


@pytest.mark.asyncio
async def test_scenario_11():
    assert await eval_scenario_11_confirmation_execution() is True


@pytest.mark.asyncio
async def test_scenario_12():
    assert await eval_scenario_12_cancellation() is True
