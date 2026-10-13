"""The evidence index: what every published table rests on, and a check that it still does (docs/evidence.md).
No model calls, no cost.

    uv run python evidence.py            # check traces, repeats, scorer and result files against the index
    uv run python evidence.py --build    # write results/evidence.json (only when every published repeat is complete)

For each planned experiment (action_guard/evidence.py, PLANNED) and each configuration, repeat and suite: the
repeat must be there and complete (every run, none interrupted, none twice), its traces must give the digest the
index recorded, and its execution identity (manifest) must be the recorded one. The scoring code must be the one
the results were made with, and every result file the one the index recorded. A runs folder that is not here at
all (a fresh clone) is listed as not checked: extract the trace archive first (README, "Reproduce the tables").
"""

import argparse
import json
import sys
import warnings
from datetime import date
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suites

from action_guard.evidence import (
    INDEX,
    PLANNED,
    execution_identity,
    result_hashes,
    scoring_identity,
    trace_digest,
)
from action_guard.metrics import cohort_problems, load_runs, unreadable_traces
from action_guard.settings import BENCHMARK_VERSION

warnings.filterwarnings("ignore", category=DeprecationWarning)


def survey(root: Path) -> tuple[list[dict], list[str], list[str]]:
    """Every planned experiment as it is on disk: (experiments, problems, folders not here)."""
    suites = get_suites(BENCHMARK_VERSION)
    experiments, problems, absent = [], [], []
    for planned in PLANNED:
        runs_dir = root / planned.runs_dir
        entry = {
            "runs_dir": planned.runs_dir,
            "attack": planned.attack,
            "status": planned.status,
            "what": planned.what,
            "cells": [],
        }
        experiments.append(entry)
        if not runs_dir.exists():
            absent.append(planned.runs_dir)
            continue
        rows_by_config = {}
        for config, rep, suite in planned.cells():
            if config not in rows_by_config:
                rows_by_config[config] = (
                    load_runs(runs_dir, config, planned.attack) if (runs_dir / config).exists() else []
                )
            rows = [r for r in rows_by_config[config] if r["rep"] == rep and r["suite_name"] == suite]
            digest, count = trace_digest(runs_dir, config, rep, suite)
            where = f"{planned.runs_dir}/{config}/{rep} {suite}"
            if not count:
                if planned.status == "published":
                    problems.append(f"{where}: the planned repeat is missing")
                continue
            incomplete = cohort_problems(rows, suites, unreadable_traces(runs_dir, config, suite))
            if planned.status == "published":
                problems += [f"{planned.runs_dir}/{config}: {p}" for p in incomplete]
            entry["cells"].append(
                {
                    "config": config,
                    "rep": rep,
                    "suite": suite,
                    "traces": count,
                    "complete": not incomplete,
                    "execution": execution_identity(runs_dir, config, rep),
                    "digest": digest,
                }
            )
    return experiments, problems, absent


def compare(index: dict, experiments: list[dict], scoring: dict, results: dict, absent: list[str]) -> list[str]:
    """What differs from the index: a changed or vanished trace set, another execution, scorer or result file."""
    problems = []
    recorded = {
        (e["runs_dir"], c["config"], c["rep"], c["suite"]): c
        for e in index["experiments"]
        for c in e["cells"]
        if e["status"] == "published"
    }
    present = {(e["runs_dir"], c["config"], c["rep"], c["suite"]): c for e in experiments for c in e["cells"]}
    for key, then in recorded.items():
        if key[0] in absent:
            continue
        now = present.get(key)
        where = f"{key[0]}/{key[1]}/{key[2]} {key[3]}"
        if now is None:
            problems.append(f"{where}: in the index, not on disk")
        elif now["digest"] != then["digest"]:
            problems.append(f"{where}: traces changed ({then['traces']} then, {now['traces']} now)")
        elif now["execution"] != then["execution"]:
            problems.append(f"{where}: executed by {now['execution']}, the index says {then['execution']}")
    if scoring["identity"] != index["scoring"]["identity"]:
        changed = [f for f in scoring["files"] if scoring["files"][f] != index["scoring"]["files"].get(f)]
        problems.append(
            f"the scoring code changed ({', '.join(changed) or 'AgentDojo'}): the results were made with "
            f"{index['scoring']['identity']}. Run scripts/reproduce.sh: if every result file comes out the same, "
            "rebuild the index (evidence.py --build keeps the earlier identity in its history); if one differs, "
            "the change goes to a new results version, never over the published ones"
        )
    for path, digest in index["results"].items():
        if path not in results:
            problems.append(f"{path}: in the index, not on disk")
        elif results[path] != digest:
            problems.append(f"{path}: changed since the index was built")
    return problems


def scoring_history(earlier: dict, scoring: dict) -> list[dict]:
    """The scorers the index recorded before this one. A changed scorer reaches a rebuilt index only after every
    published result came out the same with it (write_result refuses a changed one): the earlier identity is kept,
    with the day it was last the index's."""
    history = list(earlier.get("scoring_history", []))
    if earlier.get("scoring") and earlier["scoring"]["identity"] != scoring["identity"]:
        history.append({**earlier["scoring"], "until": earlier.get("built")})
    return history


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true", help="write the index (refused while a published repeat is not)")
    args = ap.parse_args()

    root = Path(".")
    experiments, problems, absent = survey(root)
    published = {planned.runs_dir for planned in PLANNED if planned.status == "published"}
    unchecked = sorted(set(absent) & published)  # a planned experiment that has not run is simply pending
    scoring = scoring_identity(root)
    results = {path: digest for path, digest in result_hashes(root).items() if path != INDEX.as_posix()}
    for planned, entry in zip(PLANNED, experiments, strict=True):
        expected = sum(1 for _ in planned.cells())
        here = "not here" if entry["runs_dir"] in absent else f"{len(entry['cells'])} of {expected} repeat-suites"
        print(f"  {entry['status']:9} {entry['runs_dir']:26} {here:24} {entry['what']}")
    print(f"scoring identity {scoring['identity']} (AgentDojo {scoring['agentdojo']}); {len(results)} result files")

    if args.build:
        if problems:
            print("\nNot built: " + "\n  ".join(["", *problems]))
            return 1
        if unchecked:
            print(f"\nNot built: runs folders not here: {', '.join(unchecked)}")
            return 1
        earlier = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
        history = scoring_history(earlier, scoring)
        payload = {
            "built": date.today().isoformat(),
            "benchmark": BENCHMARK_VERSION,
            "scoring": scoring,
            "scoring_history": history,
            "experiments": experiments,
            "results": results,
        }
        INDEX.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"\nSaved {INDEX}")
        return 0

    if not INDEX.exists():
        print(f"\nNo index yet: run with --build. Problems now: {problems or 'none'}")
        return 1
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    problems += compare(index, experiments, scoring, results, absent)
    new = sorted(set(results) - set(index["results"]))
    if new:
        print(f"result files not in the index (made after it was built): {', '.join(new)}")
    if unchecked:
        print(f"not checked, runs folders not here: {', '.join(unchecked)} (extract the trace archive)")
    pending = [e["runs_dir"] for e in experiments if e["status"] == "planned"]
    if pending:
        print(f"planned, not part of the published evidence yet: {', '.join(pending)}")
    if problems:
        print("\nPROBLEMS:" + "".join(f"\n  {p}" for p in problems))
        return 1
    print("\nEvery published repeat is there and complete, its traces and execution are the recorded ones, and the")
    print("scoring code and every result file are the ones the index records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
