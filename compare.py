"""Before and after on banking: the undefended agent next to the guard with each simulated user.

    uv run python compare.py                                     # release 0.1.0 runs in runs/
    uv run python compare.py --runs-dir runs-v0.2 --configs guard-follow-warnings \
        guard-judge-follow-warnings guard-hybrid-follow-warnings   # 0.2: rules vs judge vs hybrid

The baseline always comes from runs/: the undefended agent is the same in every version.
Each configuration pools all its repeats (rates with 95% confidence intervals); the
per-repeat values below the table show how much a number moves by chance alone.
Nothing is re-run or paid. Writes results/comparison.json, or results/<version>/comparison.json.
"""

import argparse
import json
import sys
from functools import cache
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.banking import attacker_values, is_attackers
from action_guard.guard_metrics import GuardStats, summarize_guard
from action_guard.metrics import Stats, expected_runs, load_runs, summarize, tasks_needing_a_change
from action_guard.settings import BENCHMARK_VERSION

SUITE = "banking"
USERS = {
    "baseline": "none (no guard)",
    "guard-approve-all": "approves everything",
    "guard-follow-warnings": "rejects what is warned (rules)",
    "guard-oracle": "rejects only the attacker",
    "guard-reject-all": "rejects everything",
    "guard-judge-follow-warnings": "rejects what is warned (judge)",
    "guard-hybrid-follow-warnings": "rejects what is warned (hybrid)",
}
RELEASE_0_1 = ("guard-approve-all", "guard-follow-warnings", "guard-oracle", "guard-reject-all")
COLUMNS = (
    "configuration",
    "simulated user",
    "runs",
    "utility (no attack)",
    "utility under attack",
    "utility, tasks needing a change (no attack)",
    "utility under attack, tasks needing a change",
    "attack success",
    "approvals per task (no attack)",
    "approvals per run (attack)",
    "false warnings (no attack)",
    "cost per run",
)
NOISE = (("attack_success", "attack success"), ("utility_under_attack", "utility under attack"), ("utility", "utility"))


def per_run(count: int, runs: int) -> str:
    return f"{count / runs:.2f}" if runs else "-"


def row(
    config: str, user: str, stats: Stats, changing: Stats, guard: GuardStats | None, reps: int, expected: int
) -> list[str]:
    return [
        config,
        user,
        f"{stats.runs}/{expected * reps}",
        str(stats.utility),
        str(stats.utility_under_attack),
        str(changing.utility),
        str(changing.utility_under_attack),
        str(stats.attack_success),
        per_run(guard.clean_requests, guard.clean_runs) if guard else "-",
        per_run(guard.attacked_requests, guard.attacked_runs) if guard else "-",
        str(guard.legit_warned_clean) if guard else "-",
        f"${stats.usd / stats.runs:.5f}" if stats.runs else "-",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", default="runs", help="where the guarded configurations' runs are")
    ap.add_argument("--configs", nargs="+", default=list(RELEASE_0_1), choices=[c for c in USERS if c != "baseline"])
    args = ap.parse_args()
    shown = [("baseline", Path("runs"))] + [(config, Path(args.runs_dir)) for config in args.configs]

    suite = get_suite(BENCHMARK_VERSION, SUITE)
    expected = expected_runs(suite)
    needed = tasks_needing_a_change(suite)
    values = cache(lambda injection_task_id: attacker_values(suite, injection_task_id))

    def attackers(suite_name: str, injection_task_id: str, request: dict) -> bool:
        return is_attackers(request["args"], values(injection_task_id))

    table, noise, payload = [], [], {}
    for config, runs_dir in shown:
        user = USERS[config]
        rows = [r for r in load_runs(runs_dir, config) if r["suite_name"] == SUITE]
        if not rows:
            print(f"(no {SUITE} runs for {config} yet)")
            continue
        by_rep = summarize(rows)
        reps = sorted(by_rep)
        pooled_rows = [{**r, "rep": "pooled"} for r in rows]
        stats = summarize(pooled_rows)["pooled"][SUITE]
        changing = summarize([r for r in pooled_rows if r["user_task_id"] in needed])["pooled"][SUITE]
        guard = summarize_guard(pooled_rows, attackers)["pooled"][SUITE] if config != "baseline" else None
        table.append(row(config, user, stats, changing, guard, len(reps), expected))
        for key, label in NOISE:
            listed = " | ".join(f"{rep} {100 * getattr(by_rep[rep][SUITE], key).value:.1f}%" for rep in reps)
            noise.append(f"  {config:22} {label:21} {listed}")
        payload[config] = {
            "simulated_user": user,
            "runs_dir": str(runs_dir),
            "repeats": reps,
            "pooled": stats.to_dict(),
            "tasks_needing_a_change": {
                "user_tasks": needed,
                "utility": changing.utility.to_dict(),
                "utility_under_attack": changing.utility_under_attack.to_dict(),
            },
            "guard": guard.to_dict() if guard else None,
            "per_repeat": {rep: by_rep[rep][SUITE].to_dict() for rep in reps},
        }

    print(f"\n## Before and after | {SUITE} | AgentDojo {BENCHMARK_VERSION} | all repeats pooled\n")
    print("| " + " | ".join(COLUMNS) + " |")
    print("|" + "---|" * len(COLUMNS))
    for cells in table:
        print("| " + " | ".join(cells) + " |")
    print(
        "\nPercentages with descriptive pooled-run 95% confidence intervals [low-high]. "
        "approve-all is a control (the guard "
        "does not block writes\nwhen every request is approved); oracle uses privileged attacker values, "
        "but can approve other mistakes.\nThese are simulated decision policies, not bounds on real users or utility."
    )
    short = ", ".join(task_id.rsplit("_", 1)[1] for task_id in needed)
    print(
        f"\nTasks needing a change: the {len(needed)} of {len(suite.user_tasks)} user tasks ({short}) whose AgentDojo "
        "check fails if the account is left untouched.\nThe others pass without any write (questions, tasks where "
        "doing nothing is correct, checks that always pass),\nso a guard that rejects everything scores on them."
    )
    print("\n## Per repeat (how much each number moves by chance)\n")
    print("\n".join(noise))

    if all(len(info["repeats"]) for info in payload.values()):
        version = Path(args.runs_dir).name.removeprefix("runs-") if args.runs_dir != "runs" else ""
        out = Path("results") / version / "comparison.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
