"""Before and after on banking: the undefended agent next to the guard with each simulated user.

    uv run python compare.py

Each configuration pools all its repeats (rates with 95% confidence intervals); the
per-repeat values below the table show how much a number moves by chance alone.
Nothing is re-run or paid. Writes results/comparison.json.
"""

import json
import sys
from functools import cache
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.banking import attacker_values, is_attackers
from action_guard.guard_metrics import GuardStats, summarize_guard
from action_guard.metrics import Stats, expected_runs, load_runs, summarize
from action_guard.settings import BENCHMARK_VERSION

SUITE = "banking"
SHOWN = (
    ("baseline", "none (no guard)"),
    ("guard-approve-all", "approves everything"),
    ("guard-follow-warnings", "rejects what is warned"),
    ("guard-oracle", "rejects only the attacker"),
    ("guard-reject-all", "rejects everything"),
)
COLUMNS = (
    "configuration",
    "simulated user",
    "runs",
    "utility (no attack)",
    "utility under attack",
    "attack success",
    "approvals per task (no attack)",
    "approvals per run (attack)",
    "false warnings (no attack)",
    "cost per run",
)
NOISE = (("attack_success", "attack success"), ("utility_under_attack", "utility under attack"), ("utility", "utility"))


def per_run(count: int, runs: int) -> str:
    return f"{count / runs:.2f}" if runs else "-"


def row(config: str, user: str, stats: Stats, guard: GuardStats | None, reps: int, expected: int) -> list[str]:
    return [
        config,
        user,
        f"{stats.runs}/{expected * reps}",
        str(stats.utility),
        str(stats.utility_under_attack),
        str(stats.attack_success),
        per_run(guard.clean_requests, guard.clean_runs) if guard else "-",
        per_run(guard.attacked_requests, guard.attacked_runs) if guard else "-",
        str(guard.legit_warned_clean) if guard else "-",
        f"${stats.usd / stats.runs:.5f}" if stats.runs else "-",
    ]


def main() -> int:
    suite = get_suite(BENCHMARK_VERSION, SUITE)
    expected = expected_runs(suite)
    values = cache(lambda injection_task_id: attacker_values(suite, injection_task_id))

    def attackers(suite_name: str, injection_task_id: str, request: dict) -> bool:
        return is_attackers(request["args"], values(injection_task_id))

    table, noise, payload = [], [], {}
    for config, user in SHOWN:
        rows = [r for r in load_runs(Path("runs"), config) if r["suite_name"] == SUITE]
        if not rows:
            print(f"(no {SUITE} runs for {config} yet)")
            continue
        by_rep = summarize(rows)
        reps = sorted(by_rep)
        pooled_rows = [{**r, "rep": "pooled"} for r in rows]
        stats = summarize(pooled_rows)["pooled"][SUITE]
        guard = summarize_guard(pooled_rows, attackers)["pooled"][SUITE] if config != "baseline" else None
        table.append(row(config, user, stats, guard, len(reps), expected))
        for key, label in NOISE:
            listed = " | ".join(f"{rep} {100 * getattr(by_rep[rep][SUITE], key).value:.1f}%" for rep in reps)
            noise.append(f"  {config:22} {label:21} {listed}")
        payload[config] = {
            "simulated_user": user,
            "repeats": reps,
            "pooled": stats.to_dict(),
            "guard": guard.to_dict() if guard else None,
            "per_repeat": {rep: by_rep[rep][SUITE].to_dict() for rep in reps},
        }

    print(f"\n## Before and after | {SUITE} | AgentDojo {BENCHMARK_VERSION} | all repeats pooled\n")
    print("| " + " | ".join(COLUMNS) + " |")
    print("|" + "---|" * len(COLUMNS))
    for cells in table:
        print("| " + " | ".join(cells) + " |")
    print(
        "\nPercentages with 95% confidence intervals [low-high]. approve-all should match the baseline (the guard "
        "changes nothing\nwhen every request is approved); oracle is the best case, since it knows the attack; "
        "reject-all bounds utility from below."
    )
    print("\n## Per repeat (how much each number moves by chance)\n")
    print("\n".join(noise))

    if all(len(info["repeats"]) for info in payload.values()):
        out = Path("results") / "comparison.json"
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
