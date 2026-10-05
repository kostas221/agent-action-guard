"""Results of one configuration, recomputed from the saved runs (nothing is re-run, nothing is paid).

    uv run python report.py                       # baseline
    uv run python report.py --config baseline

Prints one table per repeat and, when there are several repeats, how much each
number moves between them (the noise band a guard's improvement has to beat),
then where the attacks succeed: per attacker goal, and which tools they used.
Writes results/<config>.json.
"""

import argparse
import json
import sys
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suites

from action_guard.attacks import Footprint, by_goal, footprints, goal_changes, reference_tools
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


def markdown(rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
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
        print(f"\n## {args.config} · {rep} · {', '.join(models)} · AgentDojo {BENCHMARK_VERSION} · {ATTACK}\n")
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
                listed = " · ".join(f"{rep} {value:.1f}%" for rep, value in zip(reps, values, strict=True))
                print(f"  {name:9} {label:21} {listed}  -> spread {max(values) - min(values):.1f} points")

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
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
