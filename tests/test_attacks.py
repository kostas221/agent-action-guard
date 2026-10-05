from collections import Counter
from types import SimpleNamespace

from action_guard.attacks import by_goal, footprints, goal_changes


def row(user_task, injection, utility, security, tools=()):
    return {
        "rep": "rep1",
        "suite_name": "banking",
        "user_task_id": user_task,
        "injection_task_id": injection,
        "attack_type": None if injection is None else "important_instructions",
        "utility": utility,
        "security": security,
        "error": None,
        "tools": list(tools),
    }


ROWS = [
    row("user_task_0", "injection_task_10", True, True, ["read_file", "send_money"]),  # silent, send_money is extra
    row("user_task_1", "injection_task_10", False, True, ["read_file"]),  # only tools the task needs
    row("user_task_0", "injection_task_2", False, False, ["read_file", "send_money"]),  # failed: no footprint
    row("user_task_1", "injection_task_2", None, None),  # interrupted, never scored
    row("injection_task_2", None, True, True),  # the attacker goal asked directly
    row("user_task_0", None, True, True),  # clean run, not part of the attack breakdown
]
REFERENCE = {"banking": {"user_task_0": {"read_file"}, "user_task_1": {"read_file", "send_money"}}}


def test_goals_are_in_numeric_order():
    assert list(by_goal(ROWS)["banking"]) == ["injection_task_2", "injection_task_10"]


def test_goal_counts_attacks_silent_attacks_and_doability():
    goals = by_goal(ROWS)["banking"]
    hijack = goals["injection_task_10"]
    assert (hijack.attack_success.hits, hijack.attack_success.n) == (2, 2)
    assert (hijack.silent_attack.hits, hijack.silent_attack.n) == (1, 2)
    failed = goals["injection_task_2"]
    assert (failed.attack_success.hits, failed.attack_success.n) == (0, 1)
    assert (failed.doable.hits, failed.doable.n) == (1, 1)


def test_footprint_counts_only_successful_attacks():
    footprint = footprints(ROWS, REFERENCE)["banking"]
    assert footprint.successes == 2
    assert footprint.within_task_tools == 1
    assert footprint.extra_tools == Counter({"send_money": 1})


def test_goal_changes_against_the_published_version():
    def suite(**goals):
        return SimpleNamespace(injection_tasks={name: SimpleNamespace(GOAL=goal) for name, goal in goals.items()})

    current = suite(injection_task_0="steal", injection_task_1="steal more", injection_task_2="new trick")
    published = suite(injection_task_0="steal", injection_task_1="steal")
    assert goal_changes(current, published) == {
        "injection_task_0": "same",
        "injection_task_1": "changed",
        "injection_task_2": "new",
    }
