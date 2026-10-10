"""Differences between configurations, with a paired bootstrap over user tasks (nothing is re-run or paid).

    uv run python bootstrap.py --runs-dir runs-v0.3-banking --configs guard-auto-follow-warnings \
        guard-auto-hybrid-follow-warnings guard-auto-hybrid-mini-follow-warnings defense-tool_filter \
        defense-repeat_user_prompt defense-spotlighting_with_delimiting

The intervals of report.py and compare.py (Wilson, pooled runs) treat every run as independent. The runs of one
user task are not: its repeats, and under attack its runs with each injection task, share the same request and
the same account. Here the unit is the user task. Each draw resamples user tasks with replacement and takes all
their runs, in every configuration, so the two sides of a difference always cover the same tasks (paired).
For each configuration and metric: the difference from the reference (the undefended agent by default), its
95% percentile interval, and per task how often the configuration did better, the same, or worse.
With 16 user tasks (9 needing a change) the intervals are wide: they describe these tasks, not banking at large.
"""

import argparse
import json
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.automatic import load_tools
from action_guard.metrics import load_runs, run_kind, tasks_needing_a_change
from action_guard.settings import ATTACK, ATTACKS, BENCHMARK_VERSION

DRAWS = 10_000
SEED = 20261010
# metric: (runs it counts, tasks it covers, lower is better)
METRICS = {
    "attack success": ("attacked", "all", True),  # AgentDojo's own score
    "attack success, on the calls carried out": ("attacked", "all", True),  # differs only where AgentDojo
    # judges a goal on the calls asked for (Slack's Fred goal), so that a call the guard rejected still counts
    "utility under attack, tasks needing a change": ("attacked", "needed", False),
    "utility, tasks needing a change (no attack)": ("clean", "needed", False),
}


def per_task(rows: Sequence[dict], kind: str, metric: str) -> dict[str, tuple[int, int]]:
    """{user task: (hits, runs)} over the scored runs of one kind."""
    counts: dict[str, list[int]] = {}
    for row in rows:
        if row["utility"] is None or run_kind(row) != kind:
            continue
        if metric == "attack success":
            hit = row["security"]
        elif metric == "attack success, on the calls carried out":
            hit = row.get("security_carried_out", row["security"])
        else:
            hit = row["utility"]
        hits_runs = counts.setdefault(row["user_task_id"], [0, 0])
        hits_runs[0] += bool(hit)
        hits_runs[1] += 1
    return {task: (hits, runs) for task, (hits, runs) in counts.items()}


def rate(counts: Mapping[str, tuple[int, int]], tasks: Sequence[str]) -> float:
    hits = sum(counts[task][0] for task in tasks)
    runs = sum(counts[task][1] for task in tasks)
    return hits / runs if runs else float("nan")


def paired_bootstrap(
    config: Mapping[str, tuple[int, int]],
    reference: Mapping[str, tuple[int, int]],
    tasks: Sequence[str],
    draws: int = DRAWS,
    seed: int = SEED,
) -> dict:
    """The difference config - reference over `tasks`, and its 95% interval from resampling the tasks."""
    rng = random.Random(seed)
    diffs = []
    for _ in range(draws):
        sample = [rng.choice(tasks) for _ in tasks]
        diffs.append(rate(config, sample) - rate(reference, sample))
    diffs.sort()
    return {
        "difference": rate(config, tasks) - rate(reference, tasks),
        "ci95": [diffs[int(0.025 * draws)], diffs[int(0.975 * draws) - 1]],
        "tasks": len(tasks),
        "draws": draws,
        "seed": seed,
    }


def compare_tasks(config, reference, tasks, lower_is_better: bool) -> dict[str, int]:
    """Per task: better, same or worse than the reference (on the task's own rate)."""
    out = {"better": 0, "same": 0, "worse": 0}
    for task in tasks:
        a, b = config[task][0] / config[task][1], reference[task][0] / reference[task][1]
        if a == b:
            out["same"] += 1
        else:
            out["better" if (a < b) == lower_is_better else "worse"] += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", default="runs-v0.3-banking")
    ap.add_argument("--configs", nargs="+", required=True)
    ap.add_argument("--reference", default="baseline", help="the configuration every difference is taken from")
    ap.add_argument("--suite", default="banking", choices=("banking", "slack"))
    ap.add_argument("--attack", default=ATTACK, choices=ATTACKS)
    args = ap.parse_args()

    suite = get_suite(BENCHMARK_VERSION, args.suite)
    guarded = [name for name, entry in load_tools(args.suite).items() if entry["effect"] == "acts"]
    needed = tasks_needing_a_change(suite, guarded)
    every = sorted(suite.user_tasks, key=lambda task_id: int(task_id.rsplit("_", 1)[1]))
    covered = {"all": every, "needed": needed}

    def runs_of(config: str) -> list[dict]:
        main_baseline = config == "baseline" and args.attack == ATTACK  # the undefended agent's runs are in runs/
        runs_dir = Path("runs") if main_baseline else Path(args.runs_dir)
        return [r for r in load_runs(runs_dir, config, args.attack) if r["suite_name"] == args.suite]

    reference_rows = runs_of(args.reference)
    payload = {"reference": args.reference, "attack": args.attack, "suite": args.suite, "differences": {}}
    print(f"\n## Paired bootstrap over user tasks | {args.suite} | {args.attack} | minus {args.reference}\n")
    print("| configuration | metric | difference | 95% interval | tasks better / same / worse |")
    print("|---|---|---|---|---|")
    for config in args.configs:
        rows = runs_of(config)
        payload["differences"][config] = {}
        for metric, (kind, which, lower_is_better) in METRICS.items():
            tasks = covered[which]
            mine, theirs = per_task(rows, kind, metric), per_task(reference_rows, kind, metric)
            if missing := [task for task in tasks if task not in mine or task not in theirs]:
                print(f"| {config} | {metric} | - | missing runs for {', '.join(missing)} | - |")
                continue
            result = paired_bootstrap(mine, theirs, tasks)
            result["per_task"] = compare_tasks(mine, theirs, tasks, lower_is_better)
            payload["differences"][config][metric] = result
            low, high = result["ci95"]
            per = result["per_task"]
            print(
                f"| {config} | {metric} | {100 * result['difference']:+.1f} | [{100 * low:+.1f}, {100 * high:+.1f}] "
                f"| {per['better']} / {per['same']} / {per['worse']} |"
            )
    print(
        f"\nPoints. Unit: the user task ({len(every)} in all, {len(needed)} needing a change); {DRAWS} draws, seed "
        f"{SEED}.\nAn interval that excludes 0 says the difference holds on these tasks resampled; with so few tasks"
        " it is no claim about banking at large."
    )

    runs_dir = Path(args.runs_dir)
    if runs_dir.name.startswith("runs-v"):  # trials and mock runs never reach results/
        name = "bootstrap.json" if args.reference == "baseline" else f"bootstrap-vs-{args.reference}.json"
        out = Path("results") / runs_dir.name.removeprefix("runs-") / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
