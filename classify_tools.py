"""Draft a suite's tools file for the automatic policy: one model call on the tools' own descriptions.

    uv run python classify_tools.py --suite slack --dry-run              # what would be sent; no cost
    uv run python classify_tools.py --suite slack                        # one call, well under a cent
    uv run python classify_tools.py --suite slack --model gpt-6-luna     # the same with another model
    uv run python classify_tools.py --suite slack --check                # the checked file against the tools; no cost

The draft (policies/drafts/<suite>-tools-<model>.json) says which tools act and what each argument of an
acting tool is. A person checks it and saves the checked file as policies/<suite>-tools.json, the only one
the guard reads, like a tool's own annotations. When a checked file exists, the draft is compared with it.
Nothing is ever replaced. docs/automatic-policy.md.

A tool declared as reading is never asked about, so that declaration is checked against what the tools do:
every call in AgentDojo's own solutions (user and injection tasks) is run on the suite's environment, and a
tool whose call changes the environment must be declared as acting (`--check`, and a test for every checked
file). It sees only what the environment records and only tools the solutions call; a tool no solution calls
is listed as unchecked.
"""

import argparse
import json
import sys
import warnings
from pathlib import Path

import openai
from agentdojo.functions_runtime import FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from dotenv import load_dotenv

from action_guard.planner import CLASSIFY_SYSTEM, classify_tools, tool_catalog
from action_guard.settings import BENCHMARK_VERSION, DEFAULT_MODEL
from action_guard.usage import UsageMeter

warnings.filterwarnings("ignore", category=DeprecationWarning)


def differences(draft: dict, checked: dict) -> list[str]:
    """Where a draft departs from the checked file: each tool's effect, then each argument's role."""
    found = []
    for tool, entry in checked.items():
        made = draft.get(tool, {"effect": "missing", "roles": {}})
        if made["effect"] != entry["effect"]:
            found.append(f"{tool}: {made['effect']}, checked {entry['effect']}")
        for argument, role in entry["roles"].items():
            if made["effect"] == entry["effect"] and made["roles"].get(argument) != role:
                found.append(f"{tool}.{argument}: {made['roles'].get(argument)}, checked {role}")
    return found


def changing_tools(suite) -> tuple[set[str], set[str]]:
    """(tools whose call changed the environment, tools called at all) over AgentDojo's solutions of every task."""
    runtime = FunctionsRuntime(suite.tools)
    changed, called = set(), set()
    for task in [*suite.user_tasks.values(), *suite.injection_tasks.values()]:
        env = suite.load_and_inject_default_environment({})
        if hasattr(task, "init_environment"):
            env = task.init_environment(env)
        for call in task.ground_truth(env.model_copy(deep=True)):
            before = env.model_dump()
            runtime.run_function(env, call.function, dict(call.args))
            called.add(call.function)
            if env.model_dump() != before:
                changed.add(call.function)
    return changed, called


def declaration_problems(tools: dict, suite) -> tuple[list[str], list[str]]:
    """(tools declared as reading that change the environment, tools no solution calls: unchecked)."""
    changed, called = changing_tools(suite)
    wrong = sorted(name for name in changed if tools.get(name, {}).get("effect") != "acts")
    unchecked = sorted({tool.name for tool in suite.tools} - called)
    return wrong, unchecked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="the banking file was drafted by gpt-4o-mini")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="the checked file against what the tools do; no cost")
    args = ap.parse_args()

    suite = get_suite(BENCHMARK_VERSION, args.suite)
    if args.check:
        checked = json.loads((Path("policies") / f"{args.suite}-tools.json").read_text(encoding="utf-8"))
        wrong, unchecked = declaration_problems(checked, suite)
        print(f"declared as reading but change the environment: {wrong or 'none'}")
        print(f"called by no solution, so not checked: {unchecked or 'none'}")
        return 1 if wrong else 0
    catalog = tool_catalog(suite.tools)
    out = Path("policies") / "drafts" / f"{args.suite}-tools-{args.model}.json"
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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    for name, entry in policy.items():
        print(f"  {name:24} {entry['effect']:5} {entry['roles']}")
    print(f"\nSaved {out} (${meter.usd:.5f}).")

    checked = Path("policies") / f"{args.suite}-tools.json"
    if not checked.exists():
        print(f"A person checks it and saves the checked file as {checked} before any run.")
        return 0
    found = differences(policy, json.loads(checked.read_text(encoding="utf-8")))
    print(f"\nAgainst the checked {checked}: {len(found)} differences")
    for line in found:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
