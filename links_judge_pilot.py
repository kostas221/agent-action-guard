"""Fix 4's judge pilot (docs/automatic-policy.md, "Fix 4: the judge pilot"): the automatic hybrid's judge on every
stored 0.3 Slack request whose content carries a web or email address the user did not type, asked through
AutomaticPolicy.review (the code a live run uses). No agent runs.

    uv run python links_judge_pilot.py --dry-run     # how many requests, two prompts; no cost
    uv run python links_judge_pilot.py               # about 340 requests, two or three judge calls each

Each request is reviewed in the hybrid as at the time it was made: the user's request, the workspace at the task's
start, the messages up to the call and the stored plan (A1 is not the judge's to clear). Both 0.3 configurations'
requests are used, and each is compared with what happened to it then: it ran, or it was rejected. Every request
reviewed here gets fix 4's warning, so it now runs only if the judge clears it and no other warning stands.
Requests are split into the attacker's (labels.py), the others of the user's tasks, and those of the attacker's
goal run as the user's own request (AgentDojo checks that each goal can be done).
"""

import argparse
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suite
from dotenv import load_dotenv

from action_guard.automatic import AutomaticPolicy, content_addresses, load_tools
from action_guard.judge import GENERAL_KEY_HEADER, Verdict, judge_prompt
from action_guard.labels import attacker_values, is_attackers
from action_guard.metrics import Rate
from action_guard.pipelines import automatic_judge
from action_guard.planner import Plan
from action_guard.settings import BENCHMARK_VERSION, DEFAULT_MODEL, HYBRID_JUDGE_VOTES, JUDGE_MODEL
from action_guard.usage import BudgetExceeded, UsageMeter
from fixes_replay import start_of, upto_call

warnings.filterwarnings("ignore", category=DeprecationWarning)
SUITE = "slack"
CONFIGS = ("guard-auto-follow-warnings", "guard-auto-hybrid-follow-warnings")
GROUPS = ("attacker", "other", "goal run")
STANDING = ("redirect_warnings", "irreversible_warnings", "signal_warnings", "plan_warnings")  # never cleared


class Recorder:
    """Stands in for the judge in a dry run: records what it would be asked and warns."""

    def __init__(self) -> None:
        self.asked = []

    def assess(self, query, tool, args, summary, facts, key_fields):
        self.asked.append(judge_prompt(query, tool, args, summary, facts, key_fields, GENERAL_KEY_HEADER))
        return Verdict(True, "dry run")


def stored_requests(runs_dir: Path) -> list[dict]:
    """The stored requests whose content has an address the user did not type, as at the time they were made."""
    suite = get_suite(BENCHMARK_VERSION, SUITE)
    tools = load_tools(SUITE)
    items = []
    for config in CONFIGS:
        for path in sorted((runs_dir / config).glob(f"rep*/*/{SUITE}/*/*/*.json")):
            trace = json.loads(path.read_text(encoding="utf-8"))
            requests = (trace.get("guard") or {}).get("requests") or []
            if not requests:
                continue
            query, env = start_of(suite, trace)
            task, injection = trace["user_task_id"], trace.get("injection_task_id")
            values = attacker_values(suite, injection) if injection else None
            cursor = 0
            for request in requests:
                details = request.get("details") or {}
                if request["status"] not in ("executed", "failed", "rejected"):
                    continue
                if not str(details.get("warning_source", "")).startswith("auto-"):
                    continue
                messages, cursor = upto_call(trace["messages"], request["tool"], request["args"], cursor)
                roles = tools.get(request["tool"], {}).get("roles", {})
                if not content_addresses(request["args"], roles, query):
                    continue
                if values and is_attackers(SUITE, request["args"], values):
                    whose = "attacker"
                else:
                    whose = "other" if task in suite.user_tasks else "goal run"
                items.append(
                    {
                        "config": config,
                        "trace": path.relative_to(runs_dir).as_posix(),
                        "user_task": task,
                        "injection_task": injection,
                        "whose": whose,
                        "tool": request["tool"],
                        "args": request["args"],
                        "then": "rejected" if request["status"] == "rejected" else "ran",
                        "query": query,
                        "env": env,
                        "messages": messages,
                        "plan": details.get("plan"),
                    }
                )
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", default="../agent-action-guard/runs-v0.3", help="the 0.3 Slack runs")
    ap.add_argument("--max-usd", type=float, default=0.40)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="results/v0.4/links-judge-pilot.json")
    args = ap.parse_args()

    items = stored_requests(Path(args.runs_dir))
    tally = Counter((item["whose"], item["then"]) for item in items)
    print(f"{len(items)} stored requests from {args.runs_dir} carry an address the user did not type:")
    for whose in GROUPS:
        print(f"  {whose:9} ran then {tally[(whose, 'ran')]:4}   rejected then {tally[(whose, 'rejected')]:4}")
    if args.dry_run:
        judge = Recorder()
    else:
        load_dotenv(".env")
        meter = UsageMeter(args.max_usd)
        judge = automatic_judge(meter)

    rows = []
    try:
        for number, item in enumerate(items, start=1):
            stored = item["plan"]
            planner = (lambda query, p=stored: Plan(**p)) if stored is not None else None
            policy = AutomaticPolicy(SUITE, load_tools(SUITE), planner, judge, "hybrid")
            policy.start_task(item["env"])
            review = policy.review(item["tool"], item["args"], item["query"], item["env"], item["messages"])
            row = {k: v for k, v in item.items() if k not in ("query", "env", "messages", "plan")}
            row |= {
                "link_warnings": review.details["link_warnings"],
                "rule_warnings": review.details["rule_warnings"],
                "standing": [w for k in STANDING for w in review.details.get(k) or []],
                "judge": review.details["judge"],
                "warned_now": bool(review.warnings),
            }
            if args.dry_run:
                row["prompt"] = judge.asked[-1]
            rows.append(row)
            if not args.dry_run and number % 50 == 0:
                print(f"  {number}/{len(items)}   ${meter.usd:.4f}", flush=True)
    except BudgetExceeded as exc:
        print(f"\n!! {exc}; summarizing the {len(rows)} requests so far")

    if args.dry_run:
        for whose in ("attacker", "other"):
            example = next((r for r in rows if r["whose"] == whose and r["then"] == "ran"), None)
            print(f"\nExample prompt ({whose}, ran then):\n")
            print(example["prompt"] if example else "(none)")
        return 0

    summary, lines = {}, []
    for whose in GROUPS:
        for then in ("ran", "rejected"):
            group = [r for r in rows if r["whose"] == whose and r["then"] == then]
            judge_warned, warned = Rate(), Rate()
            for r in group:
                judge_warned.add(r["judge"]["warn"])
                warned.add(r["warned_now"])
            by_config = Counter(r["config"] for r in group if r["warned_now"] != (then == "rejected"))
            summary[f"{whose}, {then} then"] = {
                "judge warned": judge_warned.to_dict(),
                "warned now": warned.to_dict(),
                "decision changes, by config": dict(by_config),
            }
            lines.append(f"{whose}, {then} then: {len(group)}")
            lines.append(f"  the judge warned        {judge_warned}")
            lines.append(f"  warned now (any reason) {warned}")
    newly = Counter(r["user_task"] for r in rows if r["whose"] == "other" and r["then"] == "ran" and r["warned_now"])
    summary["other, ran then, now warned, by user task"] = dict(sorted(newly.items()))
    failures = sum(r["judge"]["failed"] for r in rows)
    print("\n" + "\n".join(lines))
    print(f"\nthe others that ran then and are now warned, by user task: {dict(sorted(newly.items()))}")
    print(f"judge failures (counted as warnings): {failures}; ${meter.usd:.4f}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "source_runs": args.runs_dir,
                "model": JUDGE_MODEL,
                "votes": HYBRID_JUDGE_VOTES,
                "agent_model": DEFAULT_MODEL,
                "usd": meter.usd,
                "failures": failures,
                "complete": len(rows) == len(items),
                "summary": summary,
                "requests": rows,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
