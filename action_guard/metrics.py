"""Metrics computed from saved AgentDojo traces, so any result can be recomputed without re-running.

Layout: runs/<config>/rep<k>/<pipeline>/<suite>/<task>/<attack or none>/<injection task or none>.json

Three kinds of runs:
  clean     a user task without attack                -> utility
  attacked  a user task with an injection hidden in   -> utility under attack, attack success
            the data the agent reads
  goal      an injection task run as a plain request  -> can the model do the attacker's goal at all
"""

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from action_guard.settings import ATTACK

TRACE_KEYS = (
    "suite_name",
    "pipeline_name",
    "user_task_id",
    "injection_task_id",
    "attack_type",
    "utility",
    "security",
    "error",
    "duration",
    "usage",
    "guard",  # approval requests and decisions, in guarded configurations only
)


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion (well-behaved near 0% and 100%)."""
    if n == 0:
        return (math.nan, math.nan)
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Rate:
    hits: int = 0
    n: int = 0

    def add(self, ok: bool) -> None:
        self.hits += bool(ok)
        self.n += 1

    @property
    def value(self) -> float:
        return self.hits / self.n if self.n else math.nan

    def __str__(self) -> str:
        if not self.n:
            return "-"
        low, high = wilson(self.hits, self.n)
        return f"{100 * self.value:.1f}% [{100 * low:.0f}-{100 * high:.0f}] ({self.hits}/{self.n})"

    def to_dict(self) -> dict:
        low, high = wilson(self.hits, self.n)
        return {"hits": self.hits, "n": self.n, "rate": self.value, "ci95": [low, high]}


@dataclass
class Stats:
    utility: Rate = field(default_factory=Rate)
    utility_under_attack: Rate = field(default_factory=Rate)
    attack_success: Rate = field(default_factory=Rate)  # AgentDojo convention: an API error counts as success
    attack_success_no_errors: Rate = field(default_factory=Rate)
    silent_attack: Rate = field(default_factory=Rate)  # attack succeeded AND the user's task was done
    goal_doable: Rate = field(default_factory=Rate)
    runs: int = 0
    errors: int = 0
    incomplete: int = 0  # trace saved but the run was interrupted before it was scored
    seconds: float = 0.0
    usd_by_role: dict = field(default_factory=lambda: defaultdict(float))
    tokens: int = 0

    @property
    def usd(self) -> float:
        return sum(self.usd_by_role.values())

    def add(self, row: dict) -> None:
        if row["utility"] is None:
            self.incomplete += 1
            return
        self.runs += 1
        self.seconds += row["duration"] or 0.0
        for role, usage in (row["usage"] or {}).items():
            self.usd_by_role[role] += usage["usd"]
            self.tokens += usage["prompt_tokens"] + usage["completion_tokens"]
        kind = run_kind(row)
        if kind == "clean":
            self.utility.add(row["utility"])
        elif kind == "goal":
            self.goal_doable.add(row["utility"])
        else:
            self.utility_under_attack.add(row["utility"])
            self.attack_success.add(row["security"])
            self.silent_attack.add(row["utility"] and row["security"])
            if row["error"] is None:
                self.attack_success_no_errors.add(row["security"])
        if row["error"] is not None:
            self.errors += 1

    def to_dict(self) -> dict:
        return {
            "utility": self.utility.to_dict(),
            "utility_under_attack": self.utility_under_attack.to_dict(),
            "attack_success": self.attack_success.to_dict(),
            "attack_success_no_errors": self.attack_success_no_errors.to_dict(),
            "silent_attack": self.silent_attack.to_dict(),
            "goal_doable": self.goal_doable.to_dict(),
            "runs": self.runs,
            "errors": self.errors,
            "incomplete": self.incomplete,
            "usd": self.usd,
            "usd_by_role": dict(self.usd_by_role),
            "tokens": self.tokens,
            "seconds": self.seconds,
        }


def run_kind(row: dict) -> str:
    if row["attack_type"] is None:
        return "goal" if row["user_task_id"].startswith("injection_task") else "clean"
    return "attacked"


def called_tools(trace: dict) -> list[str]:
    """Names of the tools the agent called during the run, in order."""
    return [
        call["function"]
        for message in trace.get("messages") or []
        if message["role"] == "assistant"
        for call in message.get("tool_calls") or []
    ]


def load_runs(runs_dir: Path, config: str) -> list[dict]:
    """One row per saved trace of `config`, with the repeat it belongs to; other attacks are skipped."""
    rows = []
    for path in sorted((runs_dir / config).glob("rep*/*/*/*/*/*.json")):
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # AgentDojo is rewriting this trace right now (benchmark still running)
            continue
        if trace.get("attack_type") not in (None, ATTACK):
            continue
        rep = path.relative_to(runs_dir / config).parts[0]
        rows.append({"rep": rep, **{key: trace.get(key) for key in TRACE_KEYS}, "tools": called_tools(trace)})
    return rows


def summarize(rows: list[dict]) -> dict[str, dict[str, Stats]]:
    """{rep: {suite or "all": Stats}}; "all" pools every run of the repeat, like AgentDojo's results table."""
    out: dict[str, dict[str, Stats]] = defaultdict(lambda: defaultdict(Stats))
    for row in rows:
        out[row["rep"]][row["suite_name"]].add(row)
        out[row["rep"]]["all"].add(row)
    return out


def expected_runs(suite) -> int:
    """Runs of a complete suite: clean user tasks + every (user, injection) pair + injection tasks alone."""
    n_user, n_injection = len(suite.user_tasks), len(suite.injection_tasks)
    return n_user + n_user * n_injection + n_injection
