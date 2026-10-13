"""How far zero (or few) successful attacks reach: upper bounds that do not count repeats as independent trials.

    uv run python bounds.py --runs-dir runs-v0.4        # writes results/v0.4/bounds.json

The Wilson intervals of report.py and compare.py treat every run as independent. The three repeats of a user task
under an injection task are not: they share the request, the account and the injection. Here the unit is coarser,
and each is a one-sided 95% Clopper-Pearson upper bound on the share that the attacker wins:
- pairs: a (user task, injection task) pair counts as won if the attack succeeded in any of its repeats (banking:
  144 pairs; with no win the bound is 1 - 0.05^(1/144), about 2.1%, against Wilson's 0.9% over 432 runs);
- user tasks and attacker goals, likewise (16 and 9 in banking).
They describe these tasks and these attack templates, not attacks in general (README, "Limits"). No model calls.
"""

import argparse
import json
import math
import sys
import warnings
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.evidence import write_result
from action_guard.metrics import (
    cohort_problems,
    experiment_problems,
    load_runs,
    run_kind,
    unreadable_traces,
    wilson,
)
from action_guard.settings import ATTACK, ATTACKS, BENCHMARK_VERSION

warnings.filterwarnings("ignore", category=DeprecationWarning)
ALPHA = 0.05


def binomial_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def upper_bound(k: int, n: int, alpha: float = ALPHA) -> float:
    """One-sided Clopper-Pearson: the largest share p for which k or fewer wins in n has probability alpha or more."""
    if n == 0:
        return math.nan
    if k >= n:
        return 1.0
    if k == 0:
        return 1 - alpha ** (1 / n)
    low, high = k / n, 1.0
    for _ in range(100):
        middle = (low + high) / 2
        low, high = (middle, high) if binomial_cdf(k, n, middle) > alpha else (low, middle)
    return high


def clustered(rows: list[dict], key, field: str) -> dict:
    """Wins and units for one grouping of the attacked runs: a unit is won if any of its runs was."""
    won: dict = {}
    for row in rows:
        unit = key(row)
        won[unit] = won.get(unit, False) or bool(row[field])
    hits, n = sum(won.values()), len(won)
    return {"won": hits, "units": n, "upper95": upper_bound(hits, n)}


def bounds(rows: list[dict]) -> dict:
    attacked = [row for row in rows if row["utility"] is not None and run_kind(row) == "attacked"]
    out = {}
    for field in ("security", "security_carried_out"):
        hits = sum(bool(row[field]) for row in attacked)
        low, high = wilson(hits, len(attacked))
        out[field] = {
            "runs": {"won": hits, "units": len(attacked), "wilson95": [low, high]},
            "pairs": clustered(attacked, lambda row: (row["user_task_id"], row["injection_task_id"]), field),
            "user tasks": clustered(attacked, lambda row: row["user_task_id"], field),
            "attacker goals": clustered(attacked, lambda row: row["injection_task_id"], field),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", default="runs-v0.4")
    ap.add_argument("--configs", nargs="+", help="default: every configuration in the runs folder")
    ap.add_argument("--suite", default="banking", choices=("banking", "slack"))
    ap.add_argument("--attack", default=ATTACK, choices=ATTACKS)
    args = ap.parse_args()
    runs_dir = Path(args.runs_dir)
    configs = args.configs or sorted(path.name for path in runs_dir.iterdir() if path.is_dir() and path.name[0] != ".")
    suites = {args.suite: get_suite(BENCHMARK_VERSION, args.suite)}
    payload, problems = {"suite": args.suite, "attack": args.attack, "alpha": ALPHA, "configs": {}}, []
    print(f"| {args.suite} | runs (Wilson 95%) | pairs (upper 95%) | user tasks | attacker goals |")
    print("|---|---|---|---|---|")
    for config in configs:
        rows = [row for row in load_runs(runs_dir, config, args.attack) if row["suite_name"] == args.suite]
        if not rows:
            continue
        unreadable = unreadable_traces(runs_dir, config, args.suite)
        problems += [f"{config}: {p}" for p in experiment_problems(rows) + cohort_problems(rows, suites, unreadable)]
        result = payload["configs"][config] = bounds(rows)
        for field, label in (("security", ""), ("security_carried_out", " (carried out)")):
            b = result[field]
            if field == "security_carried_out" and b["runs"]["won"] == result["security"]["runs"]["won"]:
                continue
            runs = f"{b['runs']['won']}/{b['runs']['units']} (<= {100 * b['runs']['wilson95'][1]:.1f}%)"
            cells = [f"{b[k]['won']}/{b[k]['units']} (<= {100 * b[k]['upper95']:.1f}%)" for k in list(b)[1:]]
            print(f"| {config}{label} | {runs} | " + " | ".join(cells) + " |")
    if problems:
        print("\nPROVISIONAL, not saved: " + "; ".join(problems))
        return 0
    if not runs_dir.name.startswith("runs-v"):  # trials, mock runs and runs/ (the undefended agent) are not saved here
        return 0
    suffix = "" if args.suite == "banking" else f"-{args.suite}"
    suffix += "" if args.attack == ATTACK else f"-{args.attack}"
    out = Path("results") / runs_dir.name.removeprefix("runs-") / f"bounds{suffix}.json"
    if write_result(out, json.dumps(payload, indent=2)):
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
