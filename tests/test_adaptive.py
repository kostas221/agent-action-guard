"""The harder tests' cases with the rules only (no network, no cost): docs/adaptive-tests.md."""

import pytest

import adaptive_tests as at
from action_guard.banking import BankingPolicy

# The rules check who gets the money and the user's own details; amounts and subjects are not checked
RULES_WARN = {"A1", "A2", "D1", "D2", "D3"}


@pytest.mark.parametrize("case", at.CASES, ids=lambda case: f"{case.id}-{'attack' if case.attack else 'legit'}")
def test_the_rules_block_new_recipients_and_changed_details_only(case):
    decision = at.decide(case, BankingPolicy())
    assert decision["error"] is None
    assert decision["blocked"] == (case.id in RULES_WARN)


def test_every_attack_has_its_text_in_one_of_agentdojos_injection_points():
    points = set(at.SUITE.get_injection_vector_defaults())
    assert all(case.injections and set(case.injections) <= points for case in at.CASES if case.attack)
    assert not any(case.injections for case in at.CASES if not case.attack)
