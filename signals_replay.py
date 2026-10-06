"""The cost of the history signals W4-W5: replay them on every approval request stored in the runs.

    uv run python signals_replay.py      # no model calls, no cost; writes results/history-signals.json

Each request is checked as the guard would have checked it at the time: the user's request,
the call, and the account as it was when the task started (earlier amounts, the user's details).
A legitimate request that carried no warning and would now get one is a decision that changes
for the follow-warnings user and the hybrid. docs/adaptive-tests.md.
"""

import json
import sys
import warnings
from collections import Counter
from functools import cache
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.banking import attacker_values, is_attackers, paid_amounts, signal_reasons
from action_guard.settings import BENCHMARK_VERSION

warnings.filterwarnings("ignore", category=DeprecationWarning)
SUITE = get_suite(BENCHMARK_VERSION, "banking")
RUNS = [
    ("runs", config) for config in ("guard-approve-all", "guard-follow-warnings", "guard-oracle", "guard-reject-all")
] + [
    ("runs-v0.2", config)
    for config in ("guard-follow-warnings", "guard-judge-follow-warnings", "guard-hybrid-follow-warnings")
]
TOOLS = {tool.name: tool for tool in SUITE.tools}


@cache
def at_start(task_id: str):
    task = SUITE.user_tasks[task_id]
    env = task.init_environment(SUITE.load_and_inject_default_environment({}))
    return task.PROMPT, env, paid_amounts(env)


def main() -> int:
    totals, changed, by_config = Counter(), [], {}
    for runs_dir, config in RUNS:
        counts = Counter()
        for path in sorted(Path(runs_dir, config).glob("rep*/*/banking/user_task_*/*/*.json")):
            trace = json.loads(path.read_text(encoding="utf-8"))
            if trace.get("utility") is None or not trace.get("guard"):
                continue
            injection = trace["injection_task_id"]
            values = attacker_values(SUITE, injection) if injection else set()
            query, env, paid = at_start(trace["user_task_id"])
            for record in trace["guard"]["requests"]:
                try:  # 0.1 traces hold the agent's raw arguments; check them as 0.1.1 would validate them
                    args = TOOLS[record["tool"]].parameters.model_validate(record["args"]).model_dump()
                except Exception:
                    counts["invalid"] += 1
                    continue
                kind = "attacker" if injection and is_attackers(args, values) else "legitimate"
                signals = signal_reasons(record["tool"], args, query, env, paid)
                counts[f"{kind} requests"] += 1
                for signal in signals:
                    counts[f"{kind}: {signal.split(' (')[0].split(' your ')[0]}"] += 1
                if kind == "legitimate" and signals and not record["warnings"]:
                    changed.append(
                        {
                            "runs": f"{runs_dir}/{config}",
                            "trace": str(path.relative_to(Path(runs_dir, config))),
                            "tool": record["tool"],
                            "args": args,
                            "signals": signals,
                            "status": record["status"],
                        }
                    )
        by_config[f"{runs_dir}/{config}"] = dict(counts)
        totals.update(counts)
        print(f"\n{runs_dir}/{config}")
        for key, n in sorted(counts.items()):
            print(f"  {key:70} {n}")

    print(f"\nall runs: {totals['legitimate requests']} legitimate and {totals['attacker requests']} attacker requests")
    for key, n in sorted(totals.items()):
        if ":" in key:
            print(f"  {key:70} {n}")
    print(f"\nlegitimate requests with no warning that a signal would now warn on: {len(changed)}")
    for item in changed:
        print(f"  {item['runs']} {item['trace']} {item['tool']} {item['signals']}")
    out = Path("results/history-signals.json")
    out.write_text(json.dumps({"by_config": by_config, "decisions_changed": changed}, indent=2), encoding="utf-8")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
