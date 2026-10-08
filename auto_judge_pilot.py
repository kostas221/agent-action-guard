"""Offline check of the automatic hybrid's judge on banking, before its live runs: the general wording, asked
through AutomaticPolicy.review (the code the live run uses) about every saved request that A0 warns on.

    uv run python auto_judge_pilot.py --dry-run     # how many requests, one prompt; no cost
    uv run python auto_judge_pilot.py               # the 0.2.0 hybrid's saved requests, a few hundred calls

No agent runs, and no plan (A1 is not the judge's to clear). The same saved requests were judged live by the
0.2 hybrid's judge in banking's own wording, so the two wordings are compared on the same requests.
docs/automatic-policy.md.
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

import openai
from dotenv import load_dotenv

from action_guard.automatic import AutomaticPolicy, load_tools
from action_guard.judge import GENERAL_KEY_HEADER, GENERAL_SYSTEM, Judge, Verdict, judge_prompt
from action_guard.metrics import Rate
from action_guard.settings import DEFAULT_MODEL, HYBRID_JUDGE_VOTES, JUDGE_MODEL
from action_guard.usage import BudgetExceeded, UsageMeter
from judge_pilot import saved_requests
from planner_replay import SUITE, at_start, label

warnings.filterwarnings("ignore", category=DeprecationWarning)


class Recorder:
    """Stands in for the judge in a dry run: records what it would be asked and warns."""

    def __init__(self) -> None:
        self.asked = []

    def assess(self, query, tool, args, summary, facts, key_fields):
        self.asked.append(judge_prompt(query, tool, args, summary, facts, key_fields, GENERAL_KEY_HEADER))
        return Verdict(True, "dry run")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs-dir", default="runs-v0.2.0")
    ap.add_argument("--config", default="guard-hybrid-follow-warnings")
    ap.add_argument("--max-usd", type=float, default=0.30)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="results/auto-judge-pilot-banking.json")
    args = ap.parse_args()

    items = saved_requests(Path(args.runs_dir), args.config, SUITE)
    if args.dry_run:
        judge = Recorder()
    else:
        load_dotenv(".env")
        meter = UsageMeter(args.max_usd)
        client = meter.wrap_client(openai.OpenAI(max_retries=3), role="guard")
        judge = Judge(
            client, JUDGE_MODEL, votes=HYBRID_JUDGE_VOTES, system=GENERAL_SYSTEM, key_header=GENERAL_KEY_HEADER
        )
    policy = AutomaticPolicy("banking", load_tools("banking"), planner=None, judge=judge, mode="hybrid")

    rows = []
    try:
        for number, item in enumerate(items, start=1):
            task, env, _ = at_start(item["user_task"])
            policy.start_task(env)
            review = policy.review(item["tool"], item["args"], task.PROMPT, env, item["messages"])
            rows.append(
                {k: v for k, v in item.items() if k != "messages"}
                | {
                    "label": label(item),
                    "a0": review.details["rule_warnings"],
                    "warned": bool(review.warnings),
                    "judge": review.details["judge"],
                }
            )
            if not args.dry_run and number % 200 == 0:
                print(f"  {number}/{len(items)}   ${meter.usd:.4f}", flush=True)
    except BudgetExceeded as exc:
        print(f"\n!! {exc}; summarizing the {len(rows)} requests so far")

    asked = [r for r in rows if r["a0"]]
    print(f"{len(rows)} saved requests from {args.runs_dir}/{args.config}; A0 warns on {len(asked)}")
    if args.dry_run:
        print("\nExample prompt (the first request A0 warns on):\n")
        print(judge.asked[0] if judge.asked else "(none)")
        return 0

    summary = {}
    for who in ("attacker", "correct", "other"):
        group = [r for r in rows if r["label"] == who]
        a0, hybrid = Rate(), Rate()
        for r in group:
            a0.add(bool(r["a0"]))
            hybrid.add(r["warned"])
        live = [r for r in group if r["a0"] and r["live_judge_warned"] is not None]
        same = sum(r["live_judge_warned"] == r["judge"]["warn"] for r in live)
        summary[who] = {"a0": a0.to_dict(), "hybrid": hybrid.to_dict(), "same_as_banking_wording": [same, len(live)]}
        print(f"\n{who}: {len(group)}")
        print(f"  warned by A0             {a0}")
        print(f"  warned after the judge   {hybrid}")
        print(f"  same verdict as banking's wording in the live 0.2 run: {same} of {len(live)}")
    failures = sum(r["judge"]["failed"] for r in asked)
    print(f"\njudge failures (counted as warnings): {failures}; ${meter.usd:.4f}")
    out = Path(args.out)
    out.write_text(
        json.dumps(
            {
                "source_runs": f"{args.runs_dir}/{args.config}",
                "model": JUDGE_MODEL,
                "votes": HYBRID_JUDGE_VOTES,
                "agent_model": DEFAULT_MODEL,
                "usd": meter.usd,
                "failures": failures,
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
