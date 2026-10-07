"""Where attacks succeed: per attacker goal, and which tools the hijacked agent used.

These breakdowns pool every repeat of a configuration (more runs, steadier
counts); the per-repeat headline numbers and their noise come from metrics.py.

"Reference tools" are the tools in AgentDojo's ground-truth solution of a user
task. A successful attack that called a tool outside them left a footprint a
tool allowlist could catch; one that stayed inside them can only be caught by
checking the arguments (for example, who the money goes to).
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from action_guard.metrics import Rate, run_kind


@dataclass
class GoalStats:
    """One attacker goal (injection task) across every user task it was hidden in."""

    attack_success: Rate = field(default_factory=Rate)
    attack_success_carried_out: Rate = field(default_factory=Rate)  # judged on the calls that ran
    silent_attack: Rate = field(default_factory=Rate)
    doable: Rate = field(default_factory=Rate)  # the goal asked directly, without any user task

    def to_dict(self) -> dict:
        return {
            "attack_success": self.attack_success.to_dict(),
            "attack_success_carried_out": self.attack_success_carried_out.to_dict(),
            "silent_attack": self.silent_attack.to_dict(),
            "doable": self.doable.to_dict(),
        }


@dataclass
class Footprint:
    """Tools used by the successful attacks of one suite."""

    successes: int = 0
    within_task_tools: int = 0  # successes that called only tools the user task's reference solution calls
    extra_tools: Counter = field(default_factory=Counter)  # tool -> successes that called it outside the reference

    def to_dict(self) -> dict:
        return {
            "successes": self.successes,
            "within_task_tools": self.within_task_tools,
            "extra_tools": dict(self.extra_tools.most_common()),
        }


def task_number(task_id: str) -> int:
    return int(task_id.rsplit("_", 1)[1])


def by_goal(rows: list[dict]) -> dict[str, dict[str, GoalStats]]:
    """{suite: {injection task: GoalStats}}, injection tasks in numeric order; interrupted runs are skipped."""
    out: dict[str, dict[str, GoalStats]] = defaultdict(lambda: defaultdict(GoalStats))
    for row in rows:
        if row["utility"] is None:
            continue
        kind = run_kind(row)
        if kind == "attacked":
            goal = out[row["suite_name"]][row["injection_task_id"]]
            goal.attack_success.add(row["security"])
            goal.attack_success_carried_out.add(row.get("security_carried_out", row["security"]))
            goal.silent_attack.add(row["utility"] and row["security"])
        elif kind == "goal":
            out[row["suite_name"]][row["user_task_id"]].doable.add(row["utility"])
    return {suite: dict(sorted(goals.items(), key=lambda item: task_number(item[0]))) for suite, goals in out.items()}


def footprints(rows: list[dict], reference: dict[str, dict[str, set[str]]]) -> dict[str, Footprint]:
    """{suite: Footprint} over the successful attacks; `reference` is {suite: {user task: tools}}."""
    out: dict[str, Footprint] = defaultdict(Footprint)
    for row in rows:
        if row["utility"] is None or run_kind(row) != "attacked" or not row["security"]:
            continue
        extra = set(row["tools"]) - reference[row["suite_name"]][row["user_task_id"]]
        footprint = out[row["suite_name"]]
        footprint.successes += 1
        footprint.within_task_tools += not extra
        footprint.extra_tools.update(extra)
    return dict(out)


def reference_tools(suite) -> dict[str, set[str]]:
    """{user task: tools its ground-truth solution calls} for an AgentDojo TaskSuite."""
    env = suite.load_and_inject_default_environment({})
    return {
        task_id: {call.function for call in task.ground_truth(env.model_copy(deep=True))}
        for task_id, task in suite.user_tasks.items()
    }


def goal_changes(suite, published) -> dict[str, str]:
    """{injection task: "new" | "changed" | "same"}, each attacker goal compared with the published suite."""
    old = published.injection_tasks
    return {
        task_id: "new" if task_id not in old else "same" if old[task_id].GOAL == task.GOAL else "changed"
        for task_id, task in suite.injection_tasks.items()
    }
