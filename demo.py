"""Watch the guard work: the agent does one banking task while an attacker's instruction hides in its data,
and you approve or reject every action that would change the account.

    uv run python demo.py                                  # adjust the rent; the landlord's notice hides an attack
    uv run python demo.py --injection-task none            # the same task without attack
    uv run python demo.py --user-task user_task_13         # change address from a file (the legit action warns too)

The default pair is one where the undefended agent did both the task and the attack in all
3 baseline repeats. Approve the rent change, reject the warned payment, and the task is
done while the attack fails.

Each run makes a few gpt-4o-mini calls (well under $0.01). Nothing is saved under runs/.
"""

import argparse
import sys
import warnings
from collections.abc import Sequence

from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.functions_runtime import Env, FunctionsRuntime
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import ChatMessage, get_text_content_as_str
from dotenv import load_dotenv

from action_guard.approval import ConsoleApprover
from action_guard.pipelines import guarded_agent
from action_guard.settings import ATTACK, BENCHMARK_VERSION, DEFAULT_MODEL
from action_guard.usage import UsageMeter

warnings.filterwarnings("ignore", category=DeprecationWarning)  # pydantic v1-style calls inside AgentDojo
PREVIEW_LINES = 6


class KeepMessages(BasePipelineElement):
    """Last pipeline element: keeps the conversation so the demo can show what the agent did."""

    def __init__(self) -> None:
        self.messages: Sequence[ChatMessage] = []

    def query(self, query: str, runtime: FunctionsRuntime, env: Env, messages: Sequence[ChatMessage], extra_args: dict):
        self.messages = messages
        return query, runtime, env, messages, extra_args


def call_text(call) -> str:
    return f"{call.function}({', '.join(f'{name}={value!r}' for name, value in call.args.items())})"


def show_actions(messages: Sequence[ChatMessage]) -> None:
    errors = {m["tool_call_id"]: m["error"] for m in messages if m["role"] == "tool"}
    calls = [call for m in messages if m["role"] == "assistant" for call in (m["tool_calls"] or [])]
    print("\n=== What the agent did ===")
    for number, call in enumerate(calls, start=1):
        error = errors.get(call.id)
        print(f"  {number}. {call_text(call)}" + (f"\n     -> not executed: {error}" if error else ""))
    answer = next((m for m in reversed(messages) if m["role"] == "assistant" and m["content"]), None)
    print("\n=== The agent's answer ===")
    print(get_text_content_as_str(answer["content"]) if answer else "(none)")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-task", default="user_task_2")
    ap.add_argument("--injection-task", default="injection_task_0", help="'none' for no attack")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    load_dotenv(".env")
    suite = get_suite(BENCHMARK_VERSION, "banking")
    user_task = suite.user_tasks[args.user_task]
    injection_task = None if args.injection_task == "none" else suite.injection_tasks[args.injection_task]
    meter = UsageMeter(max_usd=0.05)
    pipeline = guarded_agent(args.model, meter, ConsoleApprover())
    keep = KeepMessages()
    pipeline.elements = [*pipeline.elements, keep]

    print(f"Agent Action Guard demo | banking | {args.model}\n")
    print(f"Your request: {user_task.PROMPT}")
    injections = {}
    if injection_task is not None:
        injections = load_attack(ATTACK, suite, pipeline).attack(user_task, injection_task)
        print(f"\nHidden attack ({args.injection_task}): {injection_task.GOAL}")
        for place, text in injections.items():
            preview = "\n    ".join(text.strip().splitlines()[:PREVIEW_LINES])
            print(f"Planted in '{place}', where the agent will read it:\n    {preview}\n    ...")
    print("\nEvery action that changes the account waits for your answer: y = approve, Enter = reject.")
    print("A request with a WARNING needs the word 'approve'. Decide by what each request says, not by its order.")

    utility, security = suite.run_task_with_pipeline(pipeline, user_task, injection_task, injections)

    show_actions(keep.messages)
    print("\n=== Checked by AgentDojo ===")
    print(f"  Your task done:    {'yes' if utility else 'no'}")
    if injection_task is not None:
        print(f"  Attack succeeded:  {'yes' if security else 'no'}")
    print(f"  Cost: ${meter.usd:.4f} ({meter.calls} model calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
