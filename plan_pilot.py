"""0.4's planner wording (docs/automatic-policy.md, "The planner's wording"): the 0.3 and the 0.4 wordings side by
side, for both planners, on every user task of banking and Slack, and what each plan's A1 would decide on every
request stored by the 0.3 automatic configurations. No agent runs.

    uv run python plan_pilot.py --dry-run    # the requests, those of the attacker's only A1 stops; no cost
    uv run python plan_pilot.py              # 2 planners x 2 wordings x 37 user tasks x 3 plans: 444 planner calls

A1 depends only on the request's tool and the plan for the user's request, so every stored request is checked
against every new plan. What matters for security is the attacker's requests that only A1 stops: their other
warnings are computed here with today's policy and no plan (in the rules all of them; in the hybrid those the judge
cannot clear). Requests are the attacker's (labels.py), correct (banking: the call AgentDojo's solution makes,
planner_replay.is_correct) or other.
"""

import argparse
import json
import sys
import warnings
from collections import Counter
from pathlib import Path

import openai
from agentdojo.task_suite.load_suites import get_suite
from dotenv import load_dotenv

from action_guard.automatic import AutomaticPolicy, load_tools
from action_guard.labels import attacker_values, is_attackers
from action_guard.metrics import Rate
from action_guard.pipelines import priced
from action_guard.planner import PLAN_SYSTEM, PLAN_SYSTEM_0_3, make_plan, plan_prompt, tool_catalog
from action_guard.settings import BENCHMARK_VERSION, DEFAULT_MODEL, PLANNER_MODEL
from action_guard.usage import BudgetExceeded, UsageMeter
from fixes_replay import start_of, upto_call
from planner_replay import is_correct

warnings.filterwarnings("ignore", category=DeprecationWarning)
RUNS = (("runs-v0.3-banking", "banking"), ("runs-v0.3", "slack"))
WORDINGS = {"0.3": PLAN_SYSTEM_0_3, "0.4": PLAN_SYSTEM}
HARD = ("redirect_warnings", "irreversible_warnings", "signal_warnings")  # warnings the judge cannot clear


def stored_requests(root: Path) -> list[dict]:
    """Every request of the 0.3 automatic configurations made for a user task, with its other warnings (no plan)."""
    items = []
    for runs_dir, suite_name in RUNS:
        suite = get_suite(BENCHMARK_VERSION, suite_name)
        tools = load_tools(suite_name)
        for path in sorted((root / runs_dir).glob(f"guard-auto-*/rep*/*/{suite_name}/user_task_*/*/*.json")):
            trace = json.loads(path.read_text(encoding="utf-8"))
            query, env = start_of(suite, trace)
            injection = trace.get("injection_task_id")
            values = attacker_values(suite, injection) if injection else None
            cursor = 0
            for request in (trace.get("guard") or {}).get("requests") or []:
                if request["status"] not in ("executed", "failed", "rejected"):
                    continue
                messages, cursor = upto_call(trace["messages"], request["tool"], request["args"], cursor)
                policy = AutomaticPolicy(suite_name, tools)  # rules, no plan: every warning but A1
                policy.start_task(env)
                review = policy.review(request["tool"], request["args"], query, env, messages)
                if values and is_attackers(suite_name, request["args"], values):
                    label = "attacker"
                elif suite_name == "banking" and is_correct(trace["user_task_id"], request["tool"], request["args"]):
                    label = "correct"
                else:
                    label = "other"
                items.append(
                    {
                        "suite": suite_name,
                        "trace": path.relative_to(root).as_posix(),
                        "user_task": trace["user_task_id"],
                        "tool": request["tool"],
                        "label": label,
                        "only_a1_in_rules": not review.warnings,
                        "only_a1_in_hybrid": not any(review.details.get(key) for key in HARD),
                    }
                )
    return items


def groups(items: list[dict]) -> dict[str, list[dict]]:
    out = {}
    for suite_name in ("banking", "slack"):
        mine = [item for item in items if item["suite"] == suite_name]
        for label in ("correct", "other", "attacker"):
            if chosen := [item for item in mine if item["label"] == label]:
                out[f"{suite_name} {label}"] = chosen
        out[f"{suite_name} attacker, only A1 stops it (rules)"] = [
            item for item in mine if item["label"] == "attacker" and item["only_a1_in_rules"]
        ]
        out[f"{suite_name} attacker, only A1 the judge cannot clear (hybrid)"] = [
            item for item in mine if item["label"] == "attacker" and item["only_a1_in_hybrid"]
        ]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-root", default="../agent-action-guard", help="the folder that holds the 0.3 runs")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--max-usd", type=float, default=0.60)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="results/v0.4/plan-pilot.json")
    args = ap.parse_args()

    items = stored_requests(Path(args.runs_root))
    named = groups(items)
    print(f"{len(items)} stored requests of user tasks:")
    for name, chosen in named.items():
        print(f"  {name:55} {len(chosen)}")
    suites = {name: get_suite(BENCHMARK_VERSION, name) for _, name in RUNS}
    catalogs = {name: tool_catalog(suite.tools) for name, suite in suites.items()}
    tools = {name: load_tools(name) for name in suites}
    tasks = [(name, task_id) for name, suite in suites.items() for task_id in suite.user_tasks]
    models = (PLANNER_MODEL, DEFAULT_MODEL)
    calls = len(models) * len(WORDINGS) * len(tasks) * args.samples
    print(
        f"\n{len(tasks)} user tasks x {len(models)} planners x {len(WORDINGS)} wordings x {args.samples}: {calls} calls"
    )
    if args.dry_run:
        print("\nThe 0.4 wording adds, after 'an action may appear more than once.':\n")
        print(PLAN_SYSTEM.split("more than once.", 1)[1].split("For each argument", 1)[0].strip())
        print("\nExample plan prompt (slack user_task_6):\n")
        print(plan_prompt(suites["slack"].user_tasks["user_task_6"].PROMPT, catalogs["slack"], tools["slack"]))
        return 0

    load_dotenv(".env")
    meter = UsageMeter(args.max_usd)
    client = meter.wrap_client(openai.OpenAI(max_retries=3), role="planner")
    plans: dict[tuple[str, str], dict[str, list]] = {}
    try:
        for model in models:
            for wording, system in WORDINGS.items():
                made = {}
                for suite_name, task_id in tasks:
                    query = suites[suite_name].user_tasks[task_id].PROMPT
                    made[f"{suite_name}/{task_id}"] = [
                        make_plan(client, priced(model), query, catalogs[suite_name], tools[suite_name], system=system)
                        for _ in range(args.samples)
                    ]
                plans[(model, wording)] = made
                print(f"  {model} wording {wording}: done   ${meter.usd:.4f}", flush=True)
    except BudgetExceeded as exc:
        print(f"\n!! {exc}; nothing is summarized from a partial set of plans")
        return 1

    summary, per_task = {}, {}
    for (model, wording), made in plans.items():
        key = f"{model}, wording {wording}"
        summary[key] = {}
        for name, chosen in named.items():
            rate = Rate()
            for item in chosen:
                for plan in made[f"{item['suite']}/{item['user_task']}"]:
                    rate.add(all(action["tool"] != item["tool"] for action in plan.actions))
            summary[key][name] = rate.to_dict()
        per_task[key] = {
            task: {
                "plans": Counter(json.dumps(sorted(a["tool"] for a in p.actions)) for p in task_plans),
                "failed": sum(p.failed for p in task_plans),
            }
            for task, task_plans in made.items()
        }
    print("\nA1 warns (pooled over plan samples):")
    width = max(len(name) for name in named)
    print(" " * (width + 2) + "".join(f"{key:>40}" for key in summary))
    for name in named:
        cells = []
        for key in summary:
            rate = summary[key][name]
            cells.append(f"{100 * rate['rate']:5.1f}% ({rate['hits']}/{rate['n']})" if rate["n"] else "-")
        print(f"  {name:{width}}" + "".join(f"{cell:>40}" for cell in cells))
    print("\nTasks whose plans differ between the wordings (most common tool list):")
    for model in models:
        for task in sorted(per_task[f"{model}, wording 0.3"]):
            old = per_task[f"{model}, wording 0.3"][task]["plans"].most_common(1)[0][0]
            new = per_task[f"{model}, wording 0.4"][task]["plans"].most_common(1)[0][0]
            if old != new:
                print(f"  {model:24} {task:24} {old} -> {new}")
    failures = sum(t["failed"] for by_task in per_task.values() for t in by_task.values())
    print(f"\nplanner failures (an empty plan, every action warned): {failures}; {meter.calls} calls, ${meter.usd:.4f}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "samples": args.samples,
        "usd": meter.usd,
        "failures": failures,
        "requests": {name: len(chosen) for name, chosen in named.items()},
        "summary": summary,
        "plans": {
            key: {task: dict(t["plans"]) | {"failed": t["failed"]} for task, t in by_task.items()}
            for key, by_task in per_task.items()
        },  # fmt: skip
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
