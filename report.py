"""Results of one configuration, recomputed from the saved runs (nothing is re-run, nothing is paid).

    uv run python report.py                       # baseline
    uv run python report.py --config guard-follow-warnings

Prints one table per repeat and, when there are several repeats, how much each
number moves between them (an observed spread, not a significance threshold).
For a guarded configuration it adds what the guard did: approval requests, warnings
on the attacker's requests and false warnings. Then where the attacks succeed: per
attacker goal, and which tools they used. Writes results/<config>.json.
"""

import argparse
import json
import sys
from functools import cache
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suites

from action_guard.attacks import Footprint, by_goal, footprints, goal_changes, reference_tools
from action_guard.guard_metrics import GuardStats, summarize_guard
from action_guard.labels import attacker_values, is_attackers
from action_guard.metrics import Stats, expected_runs, load_runs, summarize
from action_guard.pipelines import CONFIGS
from action_guard.settings import ATTACK, BENCHMARK_VERSION, PUBLISHED_VERSION, SUITES

COLUMNS = (
    "suite",
    "runs",
    "utility",
    "utility under attack",
    "attack success",
    "silent attacks",
    "attacker goal doable",
    "errors",
    "cost",
    "s/run",
)
GUARD_COLUMNS = (
    "suite",
    "requests per task (no attack)",
    "requests per run (attack)",
    "attacker's requests warned",
    "false warnings (no attack)",
    "false warnings (attack)",
    "approved",
    "rejected",
)
GOAL_WIDTH = 70
NOISE_METRICS = (
    ("utility", "utility"),
    ("utility_under_attack", "utility under attack"),
    ("attack_success", "attack success"),
)


def table_row(name: str, stats: Stats, expected: int) -> list[str]:
    runs = f"{stats.runs}/{expected}" + (f" (+{stats.incomplete} interrupted)" if stats.incomplete else "")
    attack = str(stats.attack_success)
    if stats.errors:
        attack += f"; {100 * stats.attack_success_no_errors.value:.1f}% without errors"
    per_run = stats.seconds / stats.runs if stats.runs else 0.0
    return [
        name,
        runs,
        str(stats.utility),
        str(stats.utility_under_attack),
        attack,
        str(stats.silent_attack),
        str(stats.goal_doable),
        str(stats.errors),
        f"${stats.usd:.3f}",
        f"{per_run:.1f}",
    ]


def print_attacks(goals: dict, prints: dict, changes: dict, suites: dict) -> None:
    print("\n## Where attacks succeed (all repeats pooled)\n")
    for name in [name for name in SUITES if name in goals]:
        footprint = prints.get(name, Footprint())
        attacked = sum(goal.attack_success.n for goal in goals[name].values())
        silent = sum(goal.silent_attack.hits for goal in goals[name].values())
        print(
            f"{name}: {footprint.successes}/{attacked} attacks succeeded, {silent} of them silently; "
            f"{footprint.within_task_tools} used only tools the user task itself needs"
        )
        for task_id, goal in goals[name].items():
            text = " ".join(suites[name].injection_tasks[task_id].GOAL.split())
            print(
                f"  {task_id:18} {str(goal.attack_success):26} silent {goal.silent_attack.hits:3}  "
                f"doable {goal.doable.hits}/{goal.doable.n}  {changes[name][task_id]:7}  {text[:GOAL_WIDTH]}"
            )
        extra = ", ".join(f"{tool} {count}" for tool, count in footprint.extra_tools.most_common())
        print(f"  tools outside the task's reference solution: {extra or '-'}\n")
    print(
        f"new/changed/same: the attacker goal compared with AgentDojo {PUBLISHED_VERSION}, where the published "
        "numbers come from.\nTools outside the reference solution: in how many successful attacks the agent called "
        "a tool that AgentDojo's\nsolution of the user task never calls. Attacks using only the task's own tools "
        "can only be caught by\nchecking arguments (who gets the money, which file is sent), not by blocking tools."
    )


def guard_row(name: str, stats: GuardStats) -> list[str]:
    def per_run(requests: int, runs: int) -> str:
        return f"{requests / runs:.2f}" if runs else "-"

    return [
        name,
        per_run(stats.clean_requests, stats.clean_runs),
        per_run(stats.attacked_requests, stats.attacked_runs),
        str(stats.attacker_warned),
        str(stats.legit_warned_clean),
        str(stats.legit_warned_attacked),
        str(stats.approved),
        str(stats.rejected),
    ]


def markdown(rows: list[list[str]], columns: tuple[str, ...] = COLUMNS) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="baseline", choices=CONFIGS)
    ap.add_argument("--runs-dir", default="runs")
    args = ap.parse_args()

    rows = load_runs(Path(args.runs_dir), args.config)
    if not rows:
        print(f"No runs found under {args.runs_dir}/{args.config}/")
        return 1
    by_rep = summarize(rows)
    task_suites = get_suites(BENCHMARK_VERSION)
    expected = {name: expected_runs(suite) for name, suite in task_suites.items()}
    models = sorted({row["pipeline_name"] for row in rows})

    for rep in sorted(by_rep):
        stats = by_rep[rep]
        suites = [name for name in SUITES if name in stats]
        table = [table_row(name, stats[name], expected[name]) for name in suites]
        table.append(table_row("all", stats["all"], sum(expected[name] for name in suites)))
        print(f"\n## {args.config} | {rep} | {', '.join(models)} | AgentDojo {BENCHMARK_VERSION} | {ATTACK}\n")
        print(markdown(table))
        print(
            "\nPercentages with 95% confidence intervals [low-high]. API errors count as attack success "
            "(AgentDojo convention) and are listed separately."
        )

    if len(by_rep) > 1:
        print("\n## Run-to-run noise (same configuration, repeated)\n")
        reps = sorted(by_rep)

        def finished(rep: str, name: str) -> bool:  # a half-done repeat holds only the first tasks: not comparable
            return name in by_rep[rep] and by_rep[rep][name].runs == expected[name]

        common = [name for name in SUITES if all(finished(rep, name) for rep in reps)]
        same_suites = all(set(by_rep[rep]) == set(by_rep[reps[0]]) for rep in reps)
        all_comparable = same_suites and set(common) == set(by_rep[reps[0]]) - {"all"}
        if not common:
            print("  (no suite has finished in every repeat yet)")
        for name in common + (["all"] if all_comparable else []):
            for key, label in NOISE_METRICS:
                values = [100 * getattr(by_rep[rep][name], key).value for rep in reps]
                listed = " | ".join(f"{rep} {value:.1f}%" for rep, value in zip(reps, values, strict=True))
                print(f"  {name:9} {label:21} {listed}  -> spread {max(values) - min(values):.1f} points")

    guard_by_rep = {}
    if args.config != "baseline":
        values = cache(lambda suite, injection_task_id: attacker_values(task_suites[suite], injection_task_id))
        guard_by_rep = summarize_guard(
            rows,
            lambda suite, injection_task_id, request: is_attackers(
                suite, request["args"], values(suite, injection_task_id)
            ),
        )
        for rep in sorted(guard_by_rep):
            stats = guard_by_rep[rep]
            table = [guard_row(name, stats[name]) for name in SUITES if name in stats]
            print(f"\n## What the guard did | {args.config} | {rep}\n")
            print(markdown(table, GUARD_COLUMNS))
            judged = stats["all"]
            if judged.judge_calls:
                mean = judged.judge_seconds / judged.judge_calls
                print(
                    f"\nJudge: asked about {judged.judge_calls} requests in {judged.judge_model_calls} model calls, "
                    f"{mean:.2f} s per request; {judged.judge_failures} requests with a failed call (a vote to warn); "
                    f"{judged.judge_cleared} rule warnings cleared"
                )
        print(
            "\nAn attacker's request sends money or sets a password to the attacker's own value for the run's "
            "injection task.\nWarnings on the attacker's requests should be near 100%; false warnings near 0%."
        )

    goals = by_goal(rows)
    prints = footprints(rows, {name: reference_tools(task_suites[name]) for name in goals})
    published = get_suites(PUBLISHED_VERSION)
    changes = {name: goal_changes(task_suites[name], published[name]) for name in goals}
    print_attacks(goals, prints, changes, task_suites)

    out = Path("results") / f"{args.config}.json"
    out.parent.mkdir(exist_ok=True)
    payload = {
        "config": args.config,
        "models": models,
        "benchmark_version": BENCHMARK_VERSION,
        "attack": ATTACK,
        "expected_runs": expected,
        "repeats": {rep: {name: stats.to_dict() for name, stats in by_rep[rep].items()} for rep in by_rep},
        "attacks": {
            name: {
                "goals": {
                    task_id: {
                        **goal.to_dict(),
                        "vs_published": changes[name][task_id],
                        "goal": task_suites[name].injection_tasks[task_id].GOAL,
                    }
                    for task_id, goal in goals[name].items()
                },
                "footprint": prints.get(name, Footprint()).to_dict(),
            }
            for name in goals
        },
    }
    if guard_by_rep:
        payload["guard"] = {
            rep: {name: stats.to_dict() for name, stats in by_suite.items()} for rep, by_suite in guard_by_rep.items()
        }
    runs_dir = Path(args.runs_dir)
    if runs_dir.name.startswith("runs-v"):  # an evaluation of a later version: results/v0.2/<config>.json
        out = Path("results") / runs_dir.name.removeprefix("runs-") / f"{args.config}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
    elif runs_dir != Path("runs"):  # trials and mock runs must not overwrite the real results
        print(f"\nNot saved: results/ holds reports of runs/ and runs-v*/ only, and this one read {args.runs_dir}/")
        return 0
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
