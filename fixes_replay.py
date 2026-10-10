"""0.4's fixes on every request stored by the 0.3 automatic configurations: which decisions change (no model calls).

    uv run python fixes_replay.py ../agent-action-guard/runs-v0.3-banking ../agent-action-guard/runs-v0.3

Each request is reviewed again as at the time it was made (the user's request, the contacts and the account's
history at the task's start, the messages up to the call, the stored plan), with the stored judge verdict where
the judge was asked. The simulated user follows the warnings, so a decision changes when a request that ran is
now warned, or the other way round. Requests are split into the attacker's (labels.py) and the others.
Writes results/v0.4/fixes-replay.json. docs/automatic-policy.md, "Version 0.4".
"""

import json
import sys
import warnings
from collections import Counter
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.automatic import CONTACTS, SIGNALS, UNEXPECTED, load_tools
from action_guard.labels import attacker_values, is_attackers
from action_guard.planner import floor_reasons
from action_guard.settings import BENCHMARK_VERSION

warnings.filterwarnings("ignore", category=DeprecationWarning)


def calls_in(messages):
    return [(i, c) for i, m in enumerate(messages) if m["role"] == "assistant" for c in (m.get("tool_calls") or [])]


def start_of(suite, trace):
    """The request and the environment at the task's start."""
    task_id = trace["user_task_id"]
    env = suite.load_and_inject_default_environment(trace.get("injections") or {})
    if task_id in suite.user_tasks:
        task = suite.user_tasks[task_id]
        return task.PROMPT, task.init_environment(env)
    return suite.injection_tasks[task_id].GOAL, env


def warned_now(request, tools, query, env, known, history, suite_name, hybrid):
    """The 0.4 decision for one stored request: warned or not, and by which of the new checks."""
    details = request.get("details") or {}
    tool, args = request["tool"], request["args"]
    roles = tools[tool]["roles"]
    a0 = floor_reasons(tool, args, query, roles, lambda value: value in known)
    redirect = []
    if "selector" in roles.values():
        targets = {name: role for name, role in roles.items() if role == "target"}
        redirect = floor_reasons(tool, args, query, targets, lambda value: value in known)
    clearable = [reason for reason in a0 if reason not in redirect]
    signals = SIGNALS[suite_name][1](tool, args, query, env, history) if suite_name in SIGNALS else []
    plan = details.get("plan")
    a1 = [] if plan is None or any(a["tool"] == tool for a in plan["actions"]) else [UNEXPECTED]
    judge = details.get("judge")
    if hybrid and clearable:
        if judge is None:  # the judge was not asked then: the 0.3 A0 differs from today's, a replay error
            raise ValueError(f"no stored verdict for a clearable warning: {tool} {args}")
        kept = clearable if judge["warn"] else []
    else:
        kept = clearable
    return bool(redirect or kept or signals or a1), {"redirect": bool(redirect), "signals": signals}


def main() -> int:
    roots = [Path(arg) for arg in sys.argv[1:]]
    counts, changed = Counter(), []
    for root in roots:
        for config_dir in sorted(root.glob("guard-auto-*")):
            config = config_dir.name
            hybrid = "hybrid" in config
            for path in sorted(config_dir.glob("rep*/*/*/*/*/*.json")):
                trace = json.loads(path.read_text(encoding="utf-8"))
                requests = (trace.get("guard") or {}).get("requests") or []
                if not requests:
                    continue
                name = trace["suite_name"]
                suite, tools = get_suite(BENCHMARK_VERSION, name), load_tools(name)
                query, env = start_of(suite, trace)
                known = CONTACTS[name](env)
                history = SIGNALS[name][0](env) if name in SIGNALS else None
                injection = trace.get("injection_task_id")
                values = attacker_values(suite, injection) if injection else None
                for request in requests:
                    if request["status"] not in ("executed", "failed", "rejected"):
                        continue
                    if not str((request.get("details") or {}).get("warning_source", "")).startswith("auto-"):
                        continue
                    whose = "attacker" if values and is_attackers(name, request["args"], values) else "other"
                    then = request["status"] == "rejected"
                    now, why = warned_now(request, tools, query, env, known, history, name, hybrid)
                    key = (name, config, whose)
                    counts[key + ("requests",)] += 1
                    counts[key + ("warned then",)] += then
                    counts[key + ("warned now",)] += now
                    if now != then:
                        counts[key + ("newly warned" if now else "no longer warned",)] += 1
                        changed.append(
                            {
                                "trace": str(path.relative_to(root)),
                                "config": config,
                                "whose": whose,
                                "tool": request["tool"],
                                "args": request["args"],
                                "now": "warned" if now else "runs",
                                **why,
                            }
                        )
    summary = {}
    for (name, config, whose, what), value in sorted(counts.items()):
        summary.setdefault(f"{name} {config}", {}).setdefault(whose, {})[what] = value
    print(json.dumps(summary, indent=1))
    print(f"\n{len(changed)} decisions change:")
    for row in changed:
        print(f"  {row['config']} {row['whose']} {row['trace']} {row['tool']} {json.dumps(row['args'])[:100]} -> "
              f"{row['now']} {row['signals'] or ('redirect' if row['redirect'] else '')}")
    out = Path("results") / "v0.4" / "fixes-replay.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "changed": changed}, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
