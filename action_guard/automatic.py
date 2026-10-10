"""The automatic policy inside the guard (0.3, docs/automatic-policy.md), for any suite with a tools file.

Approval is asked for every tool that the tools file (policies/<suite>-tools.json, made once by a model from
the tools' descriptions and checked by a person) says acts. The warnings:
- A0: who or where the action goes must be typed by the user or be someone the user already deals with,
  as at the task's start; a secret must be typed by the user;
- A1: an action the plan does not expect. The plan is made by a model from the user's request alone, once
  per task, before the first action is reviewed; a plan that fails is empty, so every action is then warned.
In the hybrid a model judge may clear an A0 warning, as it may clear W1-W3 in banking, never an A1 warning.

0.4 (docs/automatic-policy.md, "Version 0.4"):
- A0 also covers the user's own settings (role `setting`: a name, an address): typed, or equal to what the
  account holds already (SETTINGS, at the task's start); the judge may clear it.
- Warnings the judge cannot clear: a redirect (an action that changes an existing item, its tool having a
  selector, and sets who or where it goes to someone the user neither typed nor already deals with); an
  irreversible action (marked in the tools file) on a target the user did not name, known or not; and the
  account's own signals, per suite (SIGNALS): in banking W4 (an unusual amount for that account) and W5 (the
  user's own details in a payment's subject), from banking.py, fixed at the task's start.

The suite-specific code is who the user already deals with (CONTACTS), what the account itself says about the
user (SIGNALS, SETTINGS) and, for evaluation only, how to recognize the attacker's requests (labels.py).
"""

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agentdojo.types import ChatMessage

from action_guard.banking import (
    USER_FIELDS,
    Review,
    call_text,
    named_in,
    paid_amounts,
    payees,
    signal_reasons,
    source_call,
    value_forms,
)
from action_guard.planner import Plan, floor_reasons, typed

UNEXPECTED = "This action is not part of what you asked."
UNLISTED = "This tool is not in the checked list of tools: nothing is known about what it does."
IRREVERSIBLE = "This cannot be undone, and you did not name the {}."
KEY_ROLES = ("target", "secret", "setting")  # shown to the user with where each value came from
FACT_WIDTH = 80


def slack_contacts(env) -> dict[str, str]:
    """The workspace's members and channels: the people and groups the user already works with."""
    known = {user: "a member of the workspace" for user in env.slack.users}
    known.update({channel: "a channel of the workspace" for channel in env.slack.channels})
    return known


def banking_contacts(env) -> dict[str, str]:
    return {account: f"an account the user had paid: {how}" for account, how in payees(env).items()}


CONTACTS: dict[str, Callable] = {"banking": banking_contacts, "slack": slack_contacts}

# What the account says about the user beyond their contacts, per suite: (what to fix at the task's start,
# the reasons for one call). Banking: the most paid to each account (W4) and the user's own details (W5).
SIGNALS: dict[str, tuple[Callable, Callable]] = {"banking": (paid_amounts, signal_reasons)}


def banking_settings(env) -> dict[str, str]:
    user = env.user_account
    return {name: getattr(user, name) for name in USER_FIELDS}


# The user's own settings as the account holds them, per suite, by argument name: a setting equal to its current
# value is no change and needs no typing (fix 3). Slack has no setting.
SETTINGS: dict[str, Callable] = {"banking": banking_settings}


def named(value, query: str) -> bool:
    """Whether the user named this person or item, in any case ("remove alice" names Alice)."""
    return typed(value, query) or typed(str(value).lower(), query.lower())


def load_tools(suite_name: str, directory: str = "policies") -> dict:
    return json.loads(Path(directory, f"{suite_name}-tools.json").read_text(encoding="utf-8"))


def check_tools(suite_name: str, tools: Mapping[str, dict], suite_tools) -> None:
    """The tools file must describe exactly the suite's tools: a run with a stale file is refused, not guessed."""
    listed, actual = set(tools), {tool.name for tool in suite_tools}
    if listed != actual:
        raise ValueError(
            f"policies/{suite_name}-tools.json does not match the suite: missing {sorted(actual - listed)}, "
            f"not in the suite {sorted(listed - actual)}. Run classify_tools.py and check the file again."
        )


def argument_facts(args: Mapping, roles: Mapping[str, str], query: str, known: Mapping[str, str], messages) -> list:
    """Where each argument's value came from, computed by code (for the judge, which never reads tool outputs)."""
    facts = []
    for name, value in args.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            if roles.get(name) in ("target", "secret"):  # A0 warns on it: the judge must see that it is empty
                facts.append(f"{name} = {json.dumps(value)}: empty (the call sets an empty value)")
            continue
        if not value_forms(value):
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
        self.signals = SIGNALS.get(suite_name)
        self.settings = SETTINGS.get(suite_name)
        self.name = f"auto-{suite_name}-{mode}"
        self.known: dict[str, str] | None = None
        self.history = None
        self.current: dict | None = None
        self.plan: Plan | None = None
        self.plan_query: str | None = None

    def start_task(self, env) -> None:
        self.known = self.contacts(env)  # fixed now: what the agent does during the task cannot add to it
        self.history = self.signals[0](env) if self.signals else None  # the same for the account's history
        self.current = self.settings(env) if self.settings else None  # and for the user's own settings
        self.plan, self.plan_query = None, None

    def needs_approval(self, tool: str) -> bool:
        entry = self.tools.get(tool)
        return entry is None or entry.get("effect") == "acts"  # a tool the file does not list is asked about

    def plan_for(self, query: str) -> Plan | None:
        if self.planner is None:
            return None
        if self.plan is None or self.plan_query != query:
            self.plan, self.plan_query = self.planner(query), query
        return self.plan

    def review(self, tool: str, args: Mapping, query: str, env, messages: Sequence[ChatMessage]) -> Review:
        if tool not in self.tools:  # warned, and not a rule warning: the judge cannot clear it
            given = {name: value for name, value in args.items() if value is not None}
            summary = f"{tool}({json.dumps(given, ensure_ascii=False)})"
            details = {"warning_source": self.name, "rule_warnings": [], "plan_warnings": [], "unlisted": True}
            return Review(summary=summary, warnings=[UNLISTED], details={**details, "plan": None, "judge": None})
        known = self.known if self.known is not None else self.contacts(env)
        current = self.current if self.current is not None else (self.settings(env) if self.settings else None)
        entry = self.tools[tool]
        roles = entry["roles"]
        updates = "selector" in roles.values()
        # A tool that changes an existing item leaves a field as it was when given an empty value (AgentDojo's
        # update tools test `if value:`), and so does a setting: that is no change, so nothing to warn about.
        checked = {
            name: value
            for name, value in args.items()
            if not (value == "" and (roles.get(name) == "setting" or (updates and roles.get(name) == "target")))
        }
        a0 = floor_reasons(tool, checked, query, roles, lambda value: value in known, current)
        targets = {name: role for name, role in roles.items() if role == "target"}
        redirect = []  # who or where an existing item goes, moved to someone new: never cleared (fix 1)
        if updates:
            redirect = floor_reasons(tool, checked, query, targets, lambda value: value in known)
        irreversible = []  # what cannot be undone needs every target typed, known or not: never cleared (fix 5)
        if entry.get("irreversible"):
            given_targets = [name for name in targets if args.get(name) is not None]
            irreversible = [IRREVERSIBLE.format(name) for name in given_targets if not named(args[name], query)]
        clearable = [reason for reason in a0 if reason not in redirect]
        signals = []  # what the account says (W4, W5 in banking): never cleared (fix 2)
        if self.signals:
            history = self.history if self.history is not None else self.signals[0](env)
            signals = self.signals[1](tool, args, query, env, history)
        plan = self.plan_for(query)
        a1 = [] if plan is None or any(action["tool"] == tool for action in plan.actions) else [UNEXPECTED]
        facts = argument_facts(args, roles, query, known, messages)
        given = {name: value for name, value in args.items() if value is not None}
        key = [fact for fact in facts if roles.get(fact.split(" = ", 1)[0]) in KEY_ROLES]
        summary = "\n".join([f"{tool}({json.dumps(given, ensure_ascii=False)})", *key])
        details = {
            "warning_source": self.name,
            "rule_warnings": a0,
            "redirect_warnings": redirect,
            "irreversible_warnings": irreversible,
            "signal_warnings": signals,
            "plan_warnings": a1,
            "plan": plan.to_dict() if plan is not None else None,
            "judge": None,
        }
        if self.mode == "hybrid" and clearable:
            key_fields = [name for name, role in roles.items() if role in ("target", "secret")]
            verdict = self.judge.assess(query, tool, args, summary, facts, key_fields)
            details["judge"] = verdict.to_dict()
            clearable = [*clearable, f"Safety check: {verdict.reason}"] if verdict.warn else []
        warnings = [*redirect, *irreversible, *clearable, *signals, *a1]
        return Review(summary=summary, warnings=warnings, details=details)
