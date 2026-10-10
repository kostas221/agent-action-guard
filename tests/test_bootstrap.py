"""The paired bootstrap over user tasks: the same tasks on both sides of every difference."""

from bootstrap import compare_tasks, paired_bootstrap, per_task

TASKS = ["user_task_0", "user_task_1", "user_task_2"]


def row(task, utility, security=None, attacked=True):
    return {
        "user_task_id": task,
        "injection_task_id": "injection_task_0" if attacked else None,
        "attack_type": "important_instructions" if attacked else None,
        "utility": utility,
        "security": security,
    }


def test_runs_are_counted_per_task_and_kind():
    rows = [row("user_task_0", True, True), row("user_task_0", False, False), row("user_task_1", True, attacked=False)]
    assert per_task(rows, "attacked", "attack success") == {"user_task_0": (1, 2)}
    assert per_task(rows, "clean", "utility") == {"user_task_1": (1, 1)}


def test_the_same_configuration_differs_by_exactly_zero():
    counts = {task: (1, 3) for task in TASKS}
    result = paired_bootstrap(counts, counts, TASKS, draws=200)
    assert result["difference"] == 0 and result["ci95"] == [0, 0]


def test_a_difference_on_every_task_never_crosses_zero_and_counts_per_task():
    better = {task: (0, 3) for task in TASKS}
    worse = {task: (3, 3) for task in TASKS}
    result = paired_bootstrap(better, worse, TASKS, draws=200)
    assert result["difference"] == -1 and result["ci95"] == [-1, -1]
    assert compare_tasks(better, worse, TASKS, lower_is_better=True) == {"better": 3, "same": 0, "worse": 0}


def test_one_task_out_of_three_gives_an_interval_that_reaches_zero():
    config = {"user_task_0": (0, 3), "user_task_1": (3, 3), "user_task_2": (3, 3)}
    reference = {task: (3, 3) for task in TASKS}
    result = paired_bootstrap(config, reference, TASKS, draws=2000)
    assert abs(result["difference"] + 1 / 3) < 1e-9
    assert result["ci95"][0] < -0.5 and result["ci95"][1] == 0  # resamples without task 0 show no difference
