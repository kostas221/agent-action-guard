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
- Fix 4, off unless asked for (`links=True`, hybrid only): a web or email address inside a message's content that
  the user did not type goes to the judge, told where the address first appeared. Its pilot on the stored Slack
  requests found the judge warns on every such address, the article's as well as the attacker's (74 of 74
  correct requests that had run), so the live configurations leave it off; the code stays so that the pilot can
  be reproduced (docs/automatic-policy.md, "Fix 4: the pilot's results").

The suite-specific code is who the user already deals with (CONTACTS), what the account itself says about the
user (SIGNALS, SETTINGS) and, for evaluation only, how to recognize the attacker's requests (labels.py).
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agentdojo.types import ChatMessage

from action_guard.banking import (
    USER_FIELDS,
    WEB_SCHEME,
    Review,
    call_text,
    named_in,
    paid_amounts,
    payees,
    signal_reasons,
    source_call,
    value_forms,
)
from action_guard.planner import EFFECTS, ROLES, Plan, floor_reasons, typed

UNEXPECTED = "This action is not part of what you asked."
UNLISTED = "This tool is not in the checked list of tools: nothing is known about what it does."
IRREVERSIBLE = "This cannot be undone, and you did not name the {}."
LINKED = "The {} carries an address you did not give: {}."
KEY_ROLES = ("target", "secret", "setting")  # shown to the user and the judge with where each value came from
ADDRESS_FACT = "address in {}"  # the name of a fact about an address inside a content argument (fix 4)
FACT_WIDTH = 80
ADDRESSES_SHOWN = 5  # per argument, in the warning and the judge's facts; the judge sees the whole call anyway

# A web or email address inside a message (fix 4): an email, an address with its scheme, or a host on a common
# ending, with or without www. ("secure-systems-252.com/x"). labels.ADDRESS, which recognizes the attacker's
# addresses for evaluation, is kept apart: changing what the guard looks for never changes what is counted.
ENDINGS = ("com", "net", "org", "io", "co", "ai", "app", "dev", "info", "biz", "xyz", "me", "site", "online", "ly")
COUNTRIES = ("eu", "uk", "us", "de", "fr", "it", "es", "nl", "ch", "gr")
CONTENT_ADDRESS = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|https?://[^\s\"'<>()\[\]{}]*[\w/]"
    r"|(?<![\w.@/-])(?:[\w-]+\.)+(?:" + "|".join(ENDINGS + COUNTRIES) + r")(?![\w-])(?:/[^\s\"'<>()\[\]{}]*[\w/])?",
    re.IGNORECASE,
)


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


def address_typed(address: str, query: str) -> bool:
    """Whether the user wrote this address, in any case, with or without its scheme or www. (fix 4)."""
    host = re.sub(r"^www\.", "", WEB_SCHEME.sub("", address), flags=re.IGNORECASE)
    return named(address, query) or named(host, query)


def content_addresses(args: Mapping, roles: Mapping[str, str], query: str) -> dict[str, list[str]]:
    """Per content argument, the web and email addresses in it that the user did not type, once each (fix 4)."""
    found = {}
    for name, value in args.items():
        if roles.get(name) != "content" or not isinstance(value, str):
            continue
        written = dict.fromkeys(CONTENT_ADDRESS.findall(value))
        untyped = [address for address in written if not address_typed(address, query)]
        if untyped:
            found[name] = untyped
    return found


def shown(addresses: Sequence[str]) -> str:
    more = len(addresses) - ADDRESSES_SHOWN
    return ", ".join(addresses[:ADDRESSES_SHOWN]) + (f" and {more} more" if more > 0 else "")


def origin(value, query: str, messages, known: Mapping[str, str] | None = None, secret: bool = False) -> str:
    """Where a value came from, as the judge is told: the request, the user's contacts, or a tool's output."""
    if typed(value, query, secret=secret):
        return "in the user's request"
    if known is not None and str(value) in known:
        return f"someone the user already deals with: {known[str(value)]}"
    if (call := source_call(value, messages)) is not None:
        named_source = ", a source the user's request names" if named_in(call, query) else ""
        return f"not in the user's request; first appeared in the output of {call_text(call)}{named_source}"
    return "neither in the user's request nor in any tool output (made up or computed)"


TOOL_KEYS = ("effect", "roles", "irreversible")


def unique_keys(pairs: list[tuple[str, object]]) -> dict:
    """A JSON object with a key given twice is refused: json.loads would silently keep the last one."""
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError(f"a key is given twice: {sorted({key for key in keys if keys.count(key) > 1})}")
    return dict(pairs)


def load_tools(suite_name: str, directory: str = "policies") -> dict:
    text = Path(directory, f"{suite_name}-tools.json").read_text(encoding="utf-8")
    tools = json.loads(text, object_pairs_hook=unique_keys)
    validate_tools(tools)
    return tools


def tools_problems(tools: Mapping) -> list[str]:
    """What is wrong with a tools file's own shape (review of 0.4: "act" for "acts" ran a tool without approval)."""
    problems = []
    for tool, entry in tools.items():
        if not isinstance(entry, Mapping):
            problems.append(f"{tool}: not an object")
            continue
        if unknown := sorted(set(entry) - set(TOOL_KEYS)):
            problems.append(f"{tool}: unknown keys {unknown}")
        if entry.get("effect") not in EFFECTS:
            problems.append(f"{tool}: effect {entry.get('effect')!r} is not one of {list(EFFECTS)}")
        roles = entry.get("roles")
        if not isinstance(roles, Mapping):
            problems.append(f"{tool}: roles must be an object")
        else:
            problems += [
                f"{tool}.{name}: role {role!r} is not one of {list(ROLES)}"
                for name, role in roles.items()
                if role not in ROLES
            ]
        if "irreversible" in entry and (entry["irreversible"] is not True or entry.get("effect") != "acts"):
            problems.append(f"{tool}: irreversible must be true, and only on a tool that acts")
    return problems


def validate_tools(tools: Mapping) -> None:
    if problems := tools_problems(tools):
        listing = "".join(f"\n  {problem}" for problem in problems)
        raise ValueError(f"The tools file is not valid; nothing runs on it:{listing}")


def check_tools(suite_name: str, tools: Mapping[str, dict], suite_tools) -> None:
    """The tools file must describe exactly the suite's tools and their arguments: a run with a stale or partial file
    is refused, not guessed. Every argument of a tool that acts has a role, so none goes unchecked by omission."""
    validate_tools(tools)
    listed, actual = set(tools), {tool.name for tool in suite_tools}
    if listed != actual:
        raise ValueError(
            f"policies/{suite_name}-tools.json does not match the suite: missing {sorted(actual - listed)}, "
            f"not in the suite {sorted(listed - actual)}. Run classify_tools.py and check the file again."
        )
    problems = []
    for tool in suite_tools:
        arguments, roles = set(tool.parameters.model_fields), set(tools[tool.name]["roles"])
        if extra := sorted(roles - arguments):
            problems.append(f"{tool.name}: roles for arguments the tool does not have {extra}")
        if tools[tool.name]["effect"] == "acts" and (missing := sorted(arguments - roles)):
            problems.append(f"{tool.name}: arguments without a role {missing}")
    if problems:
        listing = "".join(f"\n  {problem}" for problem in problems)
        raise ValueError(f"policies/{suite_name}-tools.json does not match the suite's arguments:{listing}")


def argument_facts(
    args: Mapping,
    roles: Mapping[str, str],
    query: str,
    known: Mapping[str, str],
    messages,
    addresses: Mapping[str, Sequence[str]] | None = None,
) -> list:
    """Where each argument's value came from, computed by code (for the judge, which never reads tool outputs);
    then where each address inside a content argument came from (`addresses`, fix 4)."""
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
        written = json.dumps(value if len(str(value)) <= FACT_WIDTH else f"{str(value)[:FACT_WIDTH]}...")
        role = roles.get(name)
        where = origin(value, query, messages, known if role == "target" else None, secret=role == "secret")
        facts.append(f"{name} = {written}: {where}")
    for name, found in (addresses or {}).items():
        for address in found[:ADDRESSES_SHOWN]:
            facts.append(f"{ADDRESS_FACT.format(name)} = {json.dumps(address)}: {origin(address, query, messages)}")
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
        links: bool = False,
    ) -> None:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        if mode == "hybrid" and judge is None:
            raise ValueError("the hybrid needs a judge")
        self.suite_name = suite_name
        validate_tools(tools)  # a misspelled effect or role is refused here, never read as reading
        self.tools = tools
        self.planner = planner  # None: no plan, so no A1
        self.judge = judge
        self.mode = mode
        self.links = links  # fix 4, measured and left off (above)
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
        return entry is None or entry["effect"] != "reads"  # asked unless declared reading, a tool not listed too

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
        # An address in a message the user did not type: the judge's to read, in the hybrid, when switched on (fix 4)
        addresses = content_addresses(args, roles, query) if self.mode == "hybrid" and self.links else {}
        linked = [LINKED.format(name, shown(found)) for name, found in addresses.items()]
        clearable = [reason for reason in a0 if reason not in redirect] + linked
        signals = []  # what the account says (W4, W5 in banking): never cleared (fix 2)
        if self.signals:
            history = self.history if self.history is not None else self.signals[0](env)
            signals = self.signals[1](tool, args, query, env, history)
        plan = self.plan_for(query)
        a1 = [] if plan is None or any(action["tool"] == tool for action in plan.actions) else [UNEXPECTED]
        facts = argument_facts(args, roles, query, known, messages, addresses)
        key_fields = [name for name, role in roles.items() if role in KEY_ROLES]
        key_fields += [ADDRESS_FACT.format(name) for name in addresses]
        given = {name: value for name, value in args.items() if value is not None}
        key = [fact for fact in facts if fact.split(" = ", 1)[0] in key_fields]
        summary = "\n".join([f"{tool}({json.dumps(given, ensure_ascii=False)})", *key])
        details = {
            "warning_source": self.name,
            "rule_warnings": a0,
            "redirect_warnings": redirect,
            "irreversible_warnings": irreversible,
            "signal_warnings": signals,
            "link_warnings": linked,
            "plan_warnings": a1,
            "plan": plan.to_dict() if plan is not None else None,
            "judge": None,
        }
        if self.mode == "hybrid" and clearable:
            verdict = self.judge.assess(query, tool, args, summary, facts, key_fields)
            details["judge"] = verdict.to_dict()
            clearable = [*clearable, f"Safety check: {verdict.reason}"] if verdict.warn else []
        warnings = [*redirect, *irreversible, *clearable, *signals, *a1]
        return Review(summary=summary, warnings=warnings, details=details)
