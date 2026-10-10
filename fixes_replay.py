"""0.4's fixes on every request stored by the 0.3 automatic configurations: which decisions change (no model calls).

    uv run python fixes_replay.py ../agent-action-guard/runs-v0.3-banking ../agent-action-guard/runs-v0.3

Each request goes through today's AutomaticPolicy as at the time it was made: the user's request, the contacts,
history and settings at the task's start, the messages up to the call, the stored plan, and the stored judge
verdict where the judge was asked. Where today's policy would ask the judge and no verdict is stored (a warning
0.3 did not give), the decision is counted apart as needing the judge, never guessed. The simulated user follows
the warnings, so a decision changes when a request that ran is now warned, or the other way round. Requests are
split into the attacker's (labels.py) and the others. Writes results/v0.4/fixes-replay.json.
docs/automatic-policy.md, "Version 0.4".
"""

import json
import sys
import warnings
from collections import Counter
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.automatic import AutomaticPolicy, load_tools
from action_guard.judge import Verdict
from action_guard.labels import attacker_values, is_attackers
from action_guard.planner import Plan
from action_guard.settings import BENCHMARK_VERSION

warnings.filterwarnings("ignore", category=DeprecationWarning)
NEW = ("redirect_warnings", "irreversible_warnings", "signal_warnings")


class JudgeNeeded(Exception):
    pass


class StoredJudge:
    """The verdict the judge gave then; asking about anything it was not asked raises JudgeNeeded."""

    def __init__(self, stored: dict | None) -> None:
        self.stored = stored

    def assess(self, *args, **kwargs) -> Verdict:
        if self.stored is None:
            raise JudgeNeeded
        return Verdict(self.stored["warn"], self.stored.get("reason", ""))


def start_of(suite, trace):
    """The request and the environment at the task's start."""
    task_id = trace["user_task_id"]
    env = suite.load_and_inject_default_environment(trace.get("injections") or {})
    if task_id in suite.user_tasks:
        task = suite.user_tasks[task_id]
        return task.PROMPT, task.init_environment(env)
    return suite.injection_tasks[task_id].GOAL, env


def upto_call(messages, tool, args, start):
    """The messages up to the assistant message that made this call, searched from `start`; (messages, next)."""
    for index in range(start, len(messages)):
        message = messages[index]
        if message["role"] == "assistant":
            for call in message.get("tool_calls") or []:
                if call["function"] == tool and all(args.get(k) == v for k, v in (call.get("args") or {}).items()):
                    return messages[: index + 1], index
    return messages, start  # an earlier attempt of a retried task: only the last attempt's messages are stored


def main() -> int:
    roots = [Path(arg) for arg in sys.argv[1:]]
    counts, changed, needs_judge = Counter(), [], []
    for root in roots:
        for config_dir in sorted(root.glob("guard-auto-*")):
            config = config_dir.name
            mode = "hybrid" if "hybrid" in config else "rules"
            for path in sorted(config_dir.glob("rep*/*/*/*/*/*.json")):
                trace = json.loads(path.read_text(encoding="utf-8"))
                requests = (trace.get("guard") or {}).get("requests") or []
                if not requests:
                    continue
                name = trace["suite_name"]
                suite = get_suite(BENCHMARK_VERSION, name)
                query, env = start_of(suite, trace)
                injection = trace.get("injection_task_id")
                values = attacker_values(suite, injection) if injection else None
                cursor = 0
                for request in requests:
                    details = request.get("details") or {}
                    if request["status"] not in ("executed", "failed", "rejected"):
                        continue
                    if not str(details.get("warning_source", "")).startswith("auto-"):
                        continue
                    stored_plan = details.get("plan")
                    planner = (lambda q, p=stored_plan: Plan(**p)) if stored_plan is not None else None
                    judge = StoredJudge(details.get("judge"))
                    policy = AutomaticPolicy(name, load_tools(name), planner, judge if mode == "hybrid" else None, mode)
                    policy.start_task(env)
                    messages, cursor = upto_call(trace["messages"], request["tool"], request["args"], cursor)
                    whose = "attacker" if values and is_attackers(name, request["args"], values) else "other"
                    key = (name, config, whose)
                    counts[key + ("requests",)] += 1
                    try:
                        review = policy.review(request["tool"], request["args"], query, env, messages)
                    except JudgeNeeded:
                        counts[key + ("needs the judge",)] += 1
                        needs_judge.append(
                            {
                                "trace": str(path.relative_to(root)),
                                "config": config,
                                "whose": whose,
                                "tool": request["tool"],
                                "args": request["args"],
                            }
                        )
                        continue
                    then, now = request["status"] == "rejected", bool(review.warnings)
                    if now != then:
                        counts[key + ("newly warned" if now else "no longer warned",)] += 1
                        why = {k: review.details[k] for k in NEW if review.details.get(k)}
                        why |= {"rule_warnings": review.details["rule_warnings"]} if not why else {}
                        changed.append(
                            {
                                "trace": str(path.relative_to(root)),
                                "config": config,
                                "whose": whose,
                                "tool": request["tool"],
                                "args": request["args"],
                                "now": "warned" if now else "runs",
                                "why": why,
                            }
                        )
    summary = {}
    for (name, config, whose, what), value in sorted(counts.items()):
        summary.setdefault(f"{name} {config}", {}).setdefault(whose, {})[what] = value
    print(json.dumps(summary, indent=1))
    print(f"\n{len(changed)} decisions change:")
    for row in changed:
        print(
            f"  {row['config']} {row['whose']} {row['trace']} {row['tool']} {json.dumps(row['args'])[:90]} -> "
            f"{row['now']} {row['why']}"
        )
    print(f"\n{len(needs_judge)} requests need the judge (no stored verdict):")
    for row in needs_judge:
        print(f"  {row['config']} {row['whose']} {row['trace']} {row['tool']} {json.dumps(row['args'])[:90]}")
    out = Path("results") / "v0.4" / "fixes-replay.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "changed": changed, "needs_judge": needs_judge}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
