"""A final table is made only from complete runs (metrics.cohort_problems): report.py, compare.py and bootstrap.py
show an incomplete one as provisional and never save it over a final result."""

from agentdojo.task_suite.load_suites import get_suite

from action_guard.metrics import cohort_problems, run_cells, unreadable_traces
from compare import save

BANKING = get_suite("v1.2.2", "banking")
SUITES = {"banking": BANKING}


def complete(rep: str = "rep1") -> list[dict]:
    return [
        {"rep": rep, "suite_name": "banking", "user_task_id": user, "injection_task_id": injection, "utility": True}
        for user, injection in sorted(run_cells(BANKING), key=str)
    ]


def test_a_complete_repeat_has_no_problem():
    assert len(complete()) == 169
    assert cohort_problems(complete() + complete("rep2"), SUITES) == []


def test_missing_interrupted_repeated_and_foreign_runs_are_named():
    rows = complete()
    rows.pop()  # missing
    rows[0] = {**rows[0], "utility": None}  # saved before it was scored
    rows.append(dict(rows[1]))  # saved twice
    rows.append({**rows[2], "user_task_id": "user_task_99"})  # not one of the suite's runs
    problems = cohort_problems(rows, SUITES)
    assert [p.split(": ", 1)[1].split(" ")[0] for p in problems] == ["1", "1", "runs", "runs"]
    assert "1 of 169 runs missing" in problems[0] and "interrupted" in problems[1]
    assert "saved twice" in problems[2] and "not the suite's" in problems[3]


def test_a_trace_that_cannot_be_read_is_listed(tmp_path):
    folder = tmp_path / "baseline" / "rep1" / "model" / "banking" / "user_task_0" / "none"
    folder.mkdir(parents=True)
    (folder / "none.json").write_text('{"half": ', encoding="utf-8")
    unreadable = unreadable_traces(tmp_path, "baseline")
    assert [path.name for path in unreadable] == ["none.json"]
    assert cohort_problems([], SUITES, unreadable)[0].startswith("a trace cannot be read")
    assert unreadable_traces(tmp_path, "baseline", "slack") == []


def test_a_provisional_comparison_is_never_saved(tmp_path):
    out = tmp_path / "comparison.json"
    payload = {"baseline": {}, "guard-auto-follow-warnings": {}}
    assert not save(payload, list(payload), out, provisional=["guard-auto-follow-warnings"])
    assert not out.exists()
    assert save(payload, list(payload), out)
    assert out.exists()


def test_a_run_whose_planner_or_judge_gave_no_answer_makes_the_table_provisional():
    """0.5: an outage (a rate limit) fails closed but measures the outage, not the policy; found by hand before."""
    rows = complete()
    failed_plan = {"details": {"plan": {"actions": [], "failed": True}, "judge": None}}
    failed_judge = {"details": {"plan": {"actions": [], "failed": False}, "judge": {"warn": True, "failed": True}}}
    answered = {"details": {"plan": {"actions": [], "failed": False}, "judge": {"warn": False, "failed": False}}}
    rows[0] = {**rows[0], "guard": {"requests": [answered, failed_plan]}}
    rows[1] = {**rows[1], "guard": {"requests": [failed_judge]}}
    rows[2] = {**rows[2], "guard": {"requests": [answered]}}
    assert cohort_problems(rows, SUITES) == [
        "rep1 banking: 2 runs where the guard's planner or judge gave no answer (run them again)"
    ]
