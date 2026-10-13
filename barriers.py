"""Which part of the automatic policy stopped each request, from the stored traces alone (no model calls, no cost).

    uv run python barriers.py --runs-dir runs-v0.4      # writes results/v0.4/barriers.json

An ablation without new runs: for every approval request of the automatic configurations, the warnings that decided
it, by source. A0 (who or where, secrets, settings: typed or known), the redirect rule, the irreversible rule, the
account's signals (W4-W5), A1 (an action the plan does not list), an unlisted tool; in a hybrid, A0 counts only when
the judge kept it (or was not asked: 0.5). A request with one source is stopped by that source alone: without it, the
simulated user would have approved it. Split into the attacker's requests (labels.py) and the others, which include
the agent's own mistakes. Goal runs (the attacker's goal asked directly) are left out. docs/automatic-policy.md,
"Version 0.5".
"""

import argparse
import json
import sys
import warnings
from collections import Counter
from functools import cache
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.evidence import write_result
from action_guard.labels import attacker_values, is_attackers
from action_guard.metrics import cohort_problems, experiment_problems, load_runs, run_kind, unreadable_traces
from action_guard.settings import ATTACK, ATTACKS, BENCHMARK_VERSION

warnings.filterwarnings("ignore", category=DeprecationWarning)
SOURCES = ("A0", "redirect", "irreversible", "signals", "A1", "unlisted")


def sources(details: dict) -> list[str]:
    """The sources whose warnings a request carried in the end (the judge may have removed A0's)."""
    judge = details.get("judge")
    redirect = details.get("redirect_warnings") or []
    clearable = [reason for reason in details.get("rule_warnings") or [] if reason not in redirect]
    clearable += details.get("link_warnings") or []
    found = []
    if (clearable and (judge is None or judge["warn"])) or details.get("stakes_warnings"):  # 0.5: high stakes
        found.append("A0")
    for name, key in (("redirect", "redirect_warnings"), ("irreversible", "irreversible_warnings")):
        if details.get(key):
            found.append(name)
    if details.get("signal_warnings"):
        found.append("signals")
    if details.get("plan_warnings"):
        found.append("A1")
    if details.get("unlisted"):
        found.append("unlisted")
    return found


def attribute(rows: list[dict], suite_name: str) -> dict:
    suite = get_suite(BENCHMARK_VERSION, suite_name)
    values = cache(lambda injection: attacker_values(suite, injection))
    out = {}
    for whose in ("attacker", "other"):
        out[whose] = {"requests": 0, "warned": 0, "by source": Counter(), "only source": Counter(), "judge": Counter()}
    for row in rows:
        if row["utility"] is None or run_kind(row) == "goal":
            continue
        for request in (row.get("guard") or {}).get("requests") or []:
            details = request.get("details") or {}
            injection = row["injection_task_id"]
            attackers = injection is not None and is_attackers(suite_name, request["args"], values(injection))
            whose = "attacker" if attackers else "other"
            stats = out[whose]
            found = sources(details)
            stats["requests"] += 1
            stats["warned"] += bool(found)
            stats["by source"].update(found)
            if len(found) == 1:
                stats["only source"][found[0]] += 1
            if (judge := details.get("judge")) is not None:
                stats["judge"]["asked"] += 1
                stats["judge"]["kept" if judge["warn"] else "cleared"] += 1
                stats["judge"]["the only barrier, kept" if found == ["A0"] else "other barriers too"] += judge["warn"]
    for stats in out.values():
        for key in ("by source", "only source", "judge"):
            stats[key] = dict(sorted(stats[key].items()))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", default="runs-v0.4")
    ap.add_argument("--suite", default="banking", choices=("banking", "slack"))
    ap.add_argument("--attack", default=ATTACK, choices=ATTACKS)
    args = ap.parse_args()
    runs_dir = Path(args.runs_dir)
    configs = sorted(path.name for path in runs_dir.glob("guard-auto-*") if path.is_dir())
    payload, problems = {"suite": args.suite, "attack": args.attack, "configs": {}}, []
    suites = {args.suite: get_suite(BENCHMARK_VERSION, args.suite)}
    for config in configs:
        rows = [row for row in load_runs(runs_dir, config, args.attack) if row["suite_name"] == args.suite]
        if not rows:
            continue
        unreadable = unreadable_traces(runs_dir, config, args.suite)
        problems += [f"{config}: {p}" for p in experiment_problems(rows) + cohort_problems(rows, suites, unreadable)]
        payload["configs"][config] = attribute(rows, args.suite)
        print(f"\n## {config} | {args.suite} | {args.attack}")
        for whose, stats in payload["configs"][config].items():
            print(f"  {whose}: {stats['warned']}/{stats['requests']} warned")
            print(f"    by source:   {stats['by source']}")
            print(f"    only source: {stats['only source']}")
            if stats["judge"]:
                print(f"    judge:       {stats['judge']}")
    if problems:
        print("\nPROVISIONAL, not saved: " + "; ".join(problems))
        return 0
    if not runs_dir.name.startswith("runs-v"):  # trials and mock runs never reach results/
        return 0
    suffix = "" if args.suite == "banking" else f"-{args.suite}"
    suffix += "" if args.attack == ATTACK else f"-{args.attack}"
    out = Path("results") / runs_dir.name.removeprefix("runs-") / f"barriers{suffix}.json"
    if write_result(out, json.dumps(payload, indent=2)):
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
