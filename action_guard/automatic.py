"""The automatic policy inside the guard (0.3, docs/automatic-policy.md), for any suite with a tools file.

Approval is asked for every tool that the tools file (policies/<suite>-tools.json, made once by a model from
the tools' descriptions and checked by a person) says acts. The warnings:
- A0: who or where the action goes must be typed by the user or be someone the user already deals with,
  as at the task's start; a secret must be typed by the user;
- A1: an action the plan does not expect. The plan is made by a model from the user's request alone, once
  per task, before the first action is reviewed; a plan that fails is empty, so every action is then warned.
In the hybrid a model judge may clear an A0 warning, as it may clear W1-W3 in banking, never an A1 warning.

The only suite-specific code is who the user already deals with (CONTACTS) and, for evaluation only, how to
recognize the attacker's requests (labels.py).
"""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agentdojo.types import ChatMessage

from action_guard.banking import Review, call_text, named_in, payees, source_call, value_forms
from action_guard.planner import Plan, floor_reasons, typed

UNEXPECTED = "This action is not part of what you asked."
FACT_WIDTH = 80


def slack_contacts(env) -> dict[str, str]:
    """The workspace's members and channels: the people and groups the user already works with."""
    known = {user: "a member of the workspace" for user in env.slack.users}
    known.update({channel: "a channel of the workspace" for channel in env.slack.channels})
    return known


def banking_contacts(env) -> dict[str, str]:
    return {account: f"an account the user had paid: {how}" for account, how in payees(env).items()}


CONTACTS: dict[str, Callable] = {"banking": banking_contacts, "slack": slack_contacts}


def load_tools(suite_name: str, directory: str = "policies") -> dict:
    return json.loads(Path(directory, f"{suite_name}-tools.json").read_text(encoding="utf-8"))


def argument_facts(args: Mapping, roles: Mapping[str, str], query: str, known: Mapping[str, str], messages) -> list:
    """Where each argument's value came from, computed by code (for the judge, which never reads tool outputs)."""
    facts = []
    for name, value in args.items():
        if value is None or not value_forms(value):
            continue
        shown = json.dumps(value if len(str(value)) <= FACT_WIDTH else f"{str(value)[:FACT_WIDTH]}...")
        if typed(value, query, secret=roles.get(name) == "secret"):
            where = "in the user's request"
        elif roles.get(name) == "target" and str(value) in known:
            where = f"someone the user already deals with: {known[str(value)]}"
        elif (call := source_call(value, messages)) is not None:
            named = ", a source the user's request names" if named_in(call, query) else ""
            where = f"not in the user's request; first appeared in the output of {call_text(call)}{named}"
        else:
            where = "neither in the user's request nor in any tool output (made up or computed)"
        facts.append(f"{name} = {shown}: {where}")
    return facts


class AutomaticPolicy:
    MODES = ("rules", "hybrid")

    def __init__(
        self,
        suite_name: str,
        tools: Mapping[str, dict],
        planner: Callable[[str], Plan] | None = None,
        judge=None,
        mode: str = "rules",
    ) -> None:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        if mode == "hybrid" and judge is None:
            raise ValueError("the hybrid needs a judge")
        self.suite_name = suite_name
        self.tools = tools
        self.planner = planner  # None: no plan, so no A1
        self.judge = judge
        self.mode = mode
        self.contacts = CONTACTS[suite_name]
        self.name = f"auto-{suite_name}-{mode}"
        self.known: dict[str, str] | None = None
        self.plan: Plan | None = None
        self.plan_query: str | None = None

    def start_task(self, env) -> None:
        self.known = self.contacts(env)  # fixed now: what the agent does during the task cannot add to it
        self.plan, self.plan_query = None, None

    def needs_approval(self, tool: str) -> bool:
        return self.tools.get(tool, {}).get("effect") == "acts"

    def plan_for(self, query: str) -> Plan | None:
        if self.planner is None:
            return None
        if self.plan is None or self.plan_query != query:
            self.plan, self.plan_query = self.planner(query), query
        return self.plan

    def review(self, tool: str, args: Mapping, query: str, env, messages: Sequence[ChatMessage]) -> Review:
        known = self.known if self.known is not None else self.contacts(env)
        roles = self.tools[tool]["roles"]
        a0 = floor_reasons(tool, args, query, roles, lambda value: value in known)
        plan = self.plan_for(query)
        a1 = [] if plan is None or any(action["tool"] == tool for action in plan.actions) else [UNEXPECTED]
        facts = argument_facts(args, roles, query, known, messages)
        given = {name: value for name, value in args.items() if value is not None}
        key = [fact for fact in facts if roles.get(fact.split(" = ", 1)[0]) in ("target", "secret")]
        summary = "\n".join([f"{tool}({json.dumps(given, ensure_ascii=False)})", *key])
        details = {
            "warning_source": self.name,
            "rule_warnings": a0,
            "plan_warnings": a1,
            "plan": plan.to_dict() if plan is not None else None,
            "judge": None,
        }
        if self.mode == "hybrid" and a0:
            key_fields = [name for name, role in roles.items() if role in ("target", "secret")]
            verdict = self.judge.assess(query, tool, args, summary, facts, key_fields)
            details["judge"] = verdict.to_dict()
            a0 = [*a0, f"Safety check: {verdict.reason}"] if verdict.warn else []
        return Review(summary=summary, warnings=[*a0, *a1], details=details)
