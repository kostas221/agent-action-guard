"""Make a suite's tools file for the automatic policy: one model call on the tools' own descriptions.

    uv run python classify_tools.py --suite slack --dry-run   # what would be sent; no cost
    uv run python classify_tools.py --suite slack             # one call, well under a cent

The file (policies/<suite>-tools.json) says which tools act and what each argument of an acting tool is.
A person checks it before any run, like a tool's own annotations; an existing file is never replaced.
docs/automatic-policy.md.
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

import openai
from agentdojo.task_suite.load_suites import get_suite
from dotenv import load_dotenv

from action_guard.planner import CLASSIFY_SYSTEM, classify_tools, tool_catalog
from action_guard.settings import BENCHMARK_VERSION, DEFAULT_MODEL
from action_guard.usage import UsageMeter

warnings.filterwarnings("ignore", category=DeprecationWarning)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="the banking file was made by gpt-4o-mini too")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    catalog = tool_catalog(get_suite(BENCHMARK_VERSION, args.suite).tools)
    out = Path("policies") / f"{args.suite}-tools.json"
    if out.exists():
        print(f"{out} exists; delete it first to make a new one")
        return 1
    if args.dry_run:
        print(CLASSIFY_SYSTEM, "\n")
        print(json.dumps(catalog, indent=1))
        return 0

    load_dotenv(".env")
    meter = UsageMeter(max_usd=0.05)
    policy = classify_tools(meter.wrap_client(openai.OpenAI(max_retries=3), role="guard"), args.model, catalog)
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    for name, entry in policy.items():
        print(f"  {name:24} {entry['effect']:5} {entry['roles']}")
    print(f"\nSaved {out} (${meter.usd:.5f}). Check it before any run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
