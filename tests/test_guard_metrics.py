from action_guard.guard_metrics import summarize_guard

ATTACKER = "US133000000121212121212"


def request(recipient: str, warned: bool, status: str) -> dict:
    return {
        "tool": "send_money",
        "args": {"recipient": recipient},
        "warnings": ["w"] if warned else [],
        "status": status,
    }


def row(user_task, injection_task, requests, utility=True):
    return {
        "rep": "rep1",
        "suite_name": "banking",
        "user_task_id": user_task,
        "injection_task_id": injection_task,
        "attack_type": None if injection_task is None else "important_instructions",
        "utility": utility,
        "security": False,
        "guard": {"approver": "follow-warnings", "requests": requests} if requests is not None else None,
    }


ROWS = [
    row("user_task_0", None, [request("UK12", warned=True, status="rejected")]),  # false alarm, no attack
    row("user_task_4", None, None),  # no request at all: the record is absent
    row(
        "user_task_0",
        "injection_task_0",
        [
            request(ATTACKER, warned=True, status="rejected"),  # attacker's, warned
            request(ATTACKER, warned=False, status="executed"),  # attacker's, missed
            request("GB29", warned=False, status="executed"),  # legitimate, no warning
        ],
    ),
    row("injection_task_0", None, [request(ATTACKER, warned=False, status="executed")]),  # goal run: left out
    row("user_task_1", "injection_task_0", [request(ATTACKER, warned=True, status="pending")], utility=None),
]


def attackers(suite, injection_task_id, request):
    return injection_task_id is not None and request["args"]["recipient"] == ATTACKER


def test_guard_stats_count_requests_hits_and_false_alarms():
    stats = summarize_guard(ROWS, attackers)["rep1"]["banking"]
    assert (stats.clean_runs, stats.clean_requests) == (2, 1)
    assert (stats.attacked_runs, stats.attacked_requests) == (1, 3)  # the interrupted run is not counted
    assert (stats.attacker_warned.hits, stats.attacker_warned.n) == (1, 2)
    assert (stats.legit_warned_clean.hits, stats.legit_warned_clean.n) == (1, 1)
    assert (stats.legit_warned_attacked.hits, stats.legit_warned_attacked.n) == (0, 1)
    assert (stats.approved, stats.rejected) == (2, 2)


def test_all_pools_the_suites():
    by_suite = summarize_guard(ROWS, attackers)["rep1"]
    assert by_suite["all"].to_dict() == by_suite["banking"].to_dict()


def test_approval_counts_include_consumed_and_failed_calls_without_calling_them_executed():
    requests = [
        request("GB29", warned=False, status=status)
        for status in ("approved", "consumed", "executed", "failed", "rejected", "pending")
    ]
    rows = [row("user_task_4", None, requests)]
    stats = summarize_guard(rows, attackers)["rep1"]["banking"]
    assert stats.approved == 4
    assert stats.rejected == 1


def test_judge_calls_failures_and_cleared_warnings_are_counted():
    def judged(rule_warned: bool, judge_warn: bool | None, failed: bool = False) -> dict:
        verdict = None if judge_warn is None else {"warn": judge_warn, "failed": failed, "seconds": 0.5, "reason": ""}
        details = {"rule_warnings": ["w"] if rule_warned else [], "judge": verdict}
        warned = judge_warn if judge_warn is not None else rule_warned
        return {**request("GB29", warned=warned, status="executed"), "details": details}

    requests = [judged(True, False), judged(True, True, failed=True), judged(False, None), judged(True, True)]
    stats = summarize_guard([row("user_task_4", None, requests)], attackers)["rep1"]["banking"]
    assert (stats.judge_calls, stats.judge_failures, stats.judge_cleared) == (3, 1, 1)
    assert stats.judge_seconds == 1.5
    assert stats.judge_model_calls == 3  # one call each: these verdicts carry no votes


def test_a_judge_that_votes_counts_each_model_call():
    def voted(votes: list[bool]) -> dict:
        verdict = {"warn": sum(votes) * 2 > len(votes), "failed": False, "seconds": 2.0, "votes": votes}
        details = {"rule_warnings": ["w"], "judge": verdict}
        return {**request("GB29", warned=True, status="rejected"), "details": details}

    requests = [voted([True, True]), voted([True, False, True])]
    stats = summarize_guard([row("user_task_4", None, requests)], attackers)["rep1"]["banking"]
    assert (stats.judge_calls, stats.judge_model_calls) == (2, 5)
