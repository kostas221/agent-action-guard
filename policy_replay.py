"""Today's automatic policy on every request stored by earlier live runs: which decisions change (no model calls).

    uv run python policy_replay.py runs-v0.4 --out results/v0.5/replay-runs-v0.4.json
    uv run python policy_replay.py runs-v0.3-banking runs-v0.3 --key-roles target,secret \
        --out results/v0.5/replay-runs-v0.3.json

The check behind every change to the policy since 0.5 (fixes_replay.py did it for 0.4 alone). Each request goes
through today's AutomaticPolicy as at the time it was made: the user's request, the contacts, history and settings
at the task's start, the messages up to the call, the stored plan, and the stored judge verdict. A question the
judge was never asked (no stored verdict, or other key fields than the runs gave it: `--key-roles`, the roles the
judge saw as key fields then) is counted apart as needing the judge, never guessed. The simulated user follows the
warnings, so a decision changes when a request that ran is now warned, or the other way round. One policy replays a
whole run and is told each stored decision, as the live guard tells it (what the task approved counts toward W6);
after a decision that changes, the run went on as it was decided then. Also counted: how often today's policy asks
the judge against how often it was asked then. The same approximation as fixes_replay.py: the account is the one at
the task's start, not at the call.
"""

import argparse
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite

from action_guard.automatic import AutomaticPolicy, load_tools
from action_guard.evidence import write_result
from action_guard.judge import Verdict
from action_guard.labels import attacker_values, is_attackers
from action_guard.planner import ROLES, Plan
from action_guard.settings import BENCHMARK_VERSION
from fixes_replay import JudgeNeeded, start_of, upto_call

warnings.filterwarnings("ignore", category=DeprecationWarning)
WARNING_KEYS = ("rule_warnings", "redirect_warnings", "irreversible_warnings", "signal_warnings", "plan_warnings")


class StoredJudge:
    """The verdict the judge gave then; a question it was not asked raises JudgeNeeded. `asked` counts the questions
    today's policy puts to it."""

    def __init__(self, key_roles: tuple[str, ...]) -> None:
        self.key_roles = key_roles
        self.stored, self.asked_then, self.asked = None, [], 0

    def answering(self, stored: dict | None, roles: dict) -> None:
        """The request about to be replayed: its stored verdict and the key fields the judge saw then."""
        self.stored = stored
        self.asked_then = [name for name, role in roles.items() if role in self.key_roles]
        self.asked = 0

    def assess(self, query, tool, args, summary, facts, key_fields) -> Verdict:
        self.asked += 1
        if self.stored is None or list(key_fields) != self.asked_then:
            raise JudgeNeeded
        return Verdict(self.stored["warn"], self.stored.get("reason", ""))


def replay(roots: list[Path], key_roles: tuple[str, ...]) -> dict:
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
                tools = load_tools(name)
                query, env = start_of(suite, trace)
                injection = trace.get("injection_task_id")
                values = attacker_values(suite, injection) if injection else None
                cursor = 0
                judge, policy = StoredJudge(key_roles), None  # one policy per run: it keeps the task's approvals (W6)
                for request in requests:
                    details = request.get("details") or {}
                    if request["status"] not in ("executed", "failed", "rejected"):
                        continue
                    if not str(details.get("warning_source", "")).startswith("auto-"):
                        continue
                    if policy is None:  # the plan is made once per task: every request stores the same one
                        stored_plan = details.get("plan")
                        planner = (lambda q, p=stored_plan: Plan(**p)) if stored_plan is not None else None
                        policy = AutomaticPolicy(name, tools, planner, judge if mode == "hybrid" else None, mode)
                        policy.start_task(env)
                    judge.answering(details.get("judge"), tools.get(request["tool"], {}).get("roles", {}))
                    messages, cursor = upto_call(trace["messages"], request["tool"], request["args"], cursor)
                    whose = "attacker" if values and is_attackers(name, request["args"], values) else "other"
                    key = (name, config, whose)
                    counts[key + ("requests",)] += 1
                    counts[key + ("judge asked then",)] += details.get("judge") is not None
                    where = {
                        "trace": f"{root.as_posix()}/{path.relative_to(root).as_posix()}",
                        "config": config,
                        "whose": whose,
                        "tool": request["tool"],
                        "args": request["args"],
                    }
                    try:
                        review = policy.review(request["tool"], request["args"], query, env, messages)
                    except JudgeNeeded:
                        counts[key + ("needs the judge",)] += 1
                        needs_judge.append(where)
                        review = None
                    then = request["status"] == "rejected"
                    policy.decided(request["tool"], request["args"], approved=not then)  # as it was decided then
                    if review is None:
                        continue
                    counts[key + ("judge asked now",)] += judge.asked
                    now = bool(review.warnings)
                    if now != then:
                        counts[key + ("newly warned" if now else "no longer warned",)] += 1
                        why = {k: review.details[k] for k in WARNING_KEYS if review.details.get(k)}
                        changed.append({**where, "now": "warned" if now else "runs", "why": why})
    summary = {}
    for (name, config, whose, what), value in sorted(counts.items()):
        summary.setdefault(f"{name} {config}", {}).setdefault(whose, {})[what] = value
    return {"key_roles": list(key_roles), "summary": summary, "changed": changed, "needs_judge": needs_judge}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="runs folders holding guard-auto-* configurations")
    ap.add_argument("--out", required=True, help="where to write the result, e.g. results/v0.5/replay-runs-v0.4.json")
    ap.add_argument("--key-roles", default="target,secret,setting", help="the roles the judge saw as key fields then")
    args = ap.parse_args()
    key_roles = tuple(args.key_roles.split(","))
    if unknown := set(key_roles) - set(ROLES):
        ap.error(f"unknown roles {sorted(unknown)}")
    payload = replay([Path(root) for root in args.roots], key_roles)
    print(json.dumps(payload["summary"], indent=1))
    print(f"\n{len(payload['changed'])} decisions change:")
    for row in payload["changed"]:
        print(f"  {row['config']} {row['whose']} {row['trace']} {row['tool']} {json.dumps(row['args'])[:90]} -> "
              f"{row['now']} {row['why']}")  # fmt: skip
    print(f"\n{len(payload['needs_judge'])} requests need the judge (a question it was not asked):")
    for row in payload["needs_judge"]:
        print(f"  {row['config']} {row['whose']} {row['trace']} {row['tool']} {json.dumps(row['args'])[:90]}")
    out = Path(args.out)
    if write_result(out, json.dumps(payload, indent=2)):
        print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
