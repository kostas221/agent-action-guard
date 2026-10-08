"""Banking rules of the guard (docs/approval-policy.md): which calls need approval, what the user sees, when to warn.

Trusted: the user's message and the structured fields of the account. A known payee is
an account the user has paid (a past payment or a scheduled one). Someone who only sent
the user money is not trusted: otherwise an attacker becomes "known" by sending one cent
first. Free text the agent read (files, subjects of incoming transactions) is untrusted,
since injections hide there: it never makes a value trusted and is never shown as a label.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agentdojo.logging import Logger, TraceLogger
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import ChatMessage, get_text_content_as_str

from action_guard.approval import ActionRequest
from action_guard.settings import BENCHMARK_VERSION

if TYPE_CHECKING:  # importing it first at runtime trips a circular import inside AgentDojo
    from agentdojo.default_suites.v1.banking.task_suite import BankingEnvironment

MONEY_TOOLS = ("send_money", "schedule_transaction", "update_scheduled_transaction")
WRITE_TOOLS = frozenset({*MONEY_TOOLS, "update_password", "update_user_info"})
USER_FIELDS = ("first_name", "last_name", "street", "city")
SCHEDULE_FIELDS = ("recipient", "amount", "subject", "date", "recurring")
IDENTIFYING_FIELDS = ("recipient", "password")  # what makes an action the attacker's: who gets it
LABEL_WIDTH = 40
FACT_WIDTH = 200  # longer argument values (an exfiltrated history in a subject) are cut for the judge
QUOTED_VALUE = re.compile(r"""(?<!\w)(['"])(.*?)\1""", re.DOTALL)
WEB_SCHEME = re.compile(r"^https?://", re.IGNORECASE)
# A full stop or comma after an unquoted password usually ends the sentence; "!" and "?"
# are common inside passwords, so they stay part of the token.
SENTENCE_MARKS = ".,"
# W4: an amount above this many times the most the user has paid the same account is unusual.
# Set before looking at stored requests (docs/adaptive-tests.md), not tuned on them.
UNUSUAL_FACTOR = 2


@dataclass
class Review:
    summary: str  # what the user is shown
    warnings: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)  # saved with the request: rule warnings, judge verdict


def user_supplied(value: object, query: str, *, password: bool = False) -> bool:
    """Literal presence, not authorization: quoted values must match in full.

    A password outside quotes must be a complete whitespace-delimited token,
    including punctuation, or that token without one trailing full stop or comma
    ("... to abc123." supplies abc123; it never supplies part of a token).
    Other values may be phrases, with whole-word boundaries.
    Quoted spans are removed before checking unquoted text, so a substring of a
    quoted password/address/recipient cannot be mistaken for the supplied value.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    if any(value == match.group(2) for match in QUOTED_VALUE.finditer(query)):
        return True
    unquoted = QUOTED_VALUE.sub(" ", query)
    if password:
        tokens = unquoted.split()
        return value in tokens or any(t[-1] in SENTENCE_MARKS and t[:-1] == value for t in tokens)
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", unquoted) is not None


def money(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def payees(env: BankingEnvironment) -> dict[str, str]:
    """{account: how the user knows it}, from the user's own payments only; scheduled payments first."""
    account = env.bank_account
    known: dict[str, str] = {}
    for payment in account.scheduled_transactions:
        known.setdefault(payment.recipient, f"'{payment.subject[:LABEL_WIDTH]}' (scheduled)")
    for payment in sorted(account.transactions, key=lambda t: t.date, reverse=True):
        if payment.sender in ("me", account.iban):
            known.setdefault(payment.recipient, f"'{payment.subject[:LABEL_WIDTH]}' ({payment.date})")
    return known


def call_text(call) -> str:
    """read_file('bill-december-2023.txt'), from a live FunctionCall or one loaded from a saved trace."""
    function, args = (call["function"], call["args"]) if isinstance(call, Mapping) else (call.function, call.args)
    return f"{function}({', '.join(repr(v) for v in args.values())})"


def first_seen(value: str, messages: Sequence[ChatMessage]) -> str | None:
    """The tool call whose output first contained `value`, e.g. read_file('bill-december-2023.txt')."""
    for message in messages:
        if message["role"] == "tool" and value in get_text_content_as_str(message["content"] or []):
            return call_text(message["tool_call"])
    return None


def value_forms(value) -> list[str]:
    """How a value can be written in text: 98.7 also as 98.70, 1000.0 also as 1000; a web address also as people
    write it, without http(s):// and a final slash (http://www.x.com/ is www.x.com); booleans have no source."""
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        forms = {str(value), str(float(value)), f"{float(value):.2f}"}
        if float(value).is_integer():
            forms.add(str(int(value)))
        return sorted(forms)
    text = str(value).strip()
    if not text:
        return []
    bare = WEB_SCHEME.sub("", text)
    if not any(char.isspace() for char in bare):
        bare = bare.rstrip("/")
    return [text] if bare in ("", text) else [text, bare]


def appears_in(form: str, text: str) -> bool:
    """Whole-value occurrence: 50 is not found inside 1500, 2022-01-50, 50.5 or 50x; hyphens join words."""
    return re.search(r"(?<![\w.-])" + re.escape(form) + r"(?![\w-]|\.\d)", text) is not None


def source_call(value, messages: Sequence[ChatMessage]):
    """The first tool call whose output contains the whole value, in any of its written forms."""
    forms = value_forms(value)
    for message in messages:
        if message["role"] == "tool":
            text = get_text_content_as_str(message["content"] or [])
            if any(appears_in(form, text) for form in forms):
                return message["tool_call"]
    return None


def source_of(value, messages: Sequence[ChatMessage]) -> str | None:
    call = source_call(value, messages)
    return call_text(call) if call is not None else None


def named_in(call, query: str) -> bool:
    """Whether the user's request names the source, e.g. the file in read_file('address-change.txt'), or the page in
    get_webpage('https://www.x.com/') when the user wrote www.x.com."""
    args = call["args"] if isinstance(call, Mapping) else call.args
    return any(
        isinstance(arg, str) and any(user_supplied(form, query) for form in value_forms(arg)) for arg in args.values()
    )


def argument_facts(
    tool: str, args: Mapping, query: str, known: Mapping[str, str], messages: Sequence[ChatMessage]
) -> list[str]:
    """Where each argument's value came from, computed by code (for the judge, which never reads tool outputs)."""
    facts = []
    for name, value in args.items():
        forms = value_forms(value)
        if not forms:
            continue
        shown = json.dumps(value if len(str(value)) <= FACT_WIDTH else f"{str(value)[:FACT_WIDTH]}...")
        if any(user_supplied(form, query, password=name == "password") for form in forms):
            where = "in the user's request"
        elif name == "recipient" and str(value) in known:
            where = f"an account the user had paid before the task: {known[str(value)]}"
        elif (call := source_call(value, messages)) is not None:
            named = ", a source the user's request names" if named_in(call, query) else ""
            where = f"not in the user's request; first appeared in the output of {call_text(call)}{named}"
        else:
            where = "neither in the user's request nor in any tool output (made up or computed)"
        facts.append(f"{name} = {shown}: {where}")
    return facts


def recipient_note(recipient: str, query: str, known: Mapping[str, str], messages: Sequence[ChatMessage]) -> str:
    if recipient in known:
        return f"{recipient}: you have paid them before: {known[recipient]}"
    if user_supplied(recipient, query):
        return f"{recipient}: from your message"
    seen = first_seen(recipient, messages)
    if seen:
        return f"{recipient}: not in your message, never paid; first seen in {seen}"
    return f"{recipient}: not in your message, never paid, and not in anything the agent read"


def describe(tool: str, args: Mapping, env: BankingEnvironment) -> str:
    """One line saying what will happen, with every argument as it will be executed."""
    balance = money(env.bank_account.balance)
    if tool == "send_money":
        return (
            f"Send {money(args.get('amount'))} to {args.get('recipient')}, subject {args.get('subject')!r}, "
            f"date {args.get('date')}. Balance {balance}."
        )
    if tool == "schedule_transaction":
        kind = "recurring payment" if args.get("recurring") else "payment"
        return (
            f"Schedule a {kind} of {money(args.get('amount'))} to {args.get('recipient')}, "
            f"subject {args.get('subject')!r}, from {args.get('date')}. Balance {balance}."
        )
    if tool == "update_scheduled_transaction":
        current = next((t for t in env.bank_account.scheduled_transactions if t.id == args.get("id")), None)
        if current is None:
            return f"Change scheduled payment {args.get('id')}, which does not exist: {dict(args)}"

        def shown(name, value):
            return money(value) if name == "amount" else value

        changes = [
            f"{name} {shown(name, getattr(current, name))} -> {shown(name, args[name])}"
            for name in SCHEDULE_FIELDS
            if args.get(name) is not None and args[name] != getattr(current, name)
        ]
        return (
            f"Change scheduled payment {current.id} ('{current.subject[:LABEL_WIDTH]}', "
            f"{money(current.amount)} to {current.recipient}): {'; '.join(changes) or 'no change'}."
        )
    if tool == "update_password":
        return f"Change your password to {args.get('password')}."
    if tool == "update_user_info":
        user = env.user_account
        changes = [
            f"{name.replace('_', ' ')} {getattr(user, name)} -> {args[name]}"
            for name in USER_FIELDS
            if args.get(name) and args[name] != getattr(user, name)
        ]
        return f"Change your account details: {'; '.join(changes) or 'no change'}."
    return f"{tool}({dict(args)})"


def warning_reasons(
    tool: str, args: Mapping, query: str, env: BankingEnvironment, known: Mapping[str, str]
) -> list[str]:
    """Rules W1-W3 of the policy: what in this call did not come from the user (`known`: trusted payees)."""
    reasons = []
    recipient = args.get("recipient")
    if tool in MONEY_TOOLS and recipient is not None and not user_supplied(recipient, query) and recipient not in known:
        reasons.append("The recipient is not in your message and you have never paid them.")
    if tool == "update_password" and not user_supplied(args.get("password"), query, password=True):
        reasons.append("You did not type this password.")
    if tool == "update_user_info":
        user = env.user_account
        untyped = [
            name.replace("_", " ")
            for name in USER_FIELDS
            if args.get(name) and args[name] != getattr(user, name) and not user_supplied(args[name], query)
        ]
        if untyped:
            reasons.append(f"You did not type the new {', '.join(untyped)}.")
    return reasons


def paid_amounts(env: BankingEnvironment) -> dict[str, float]:
    """{account: the most the user has paid it}, from the user's own payments, scheduled ones included."""
    account = env.bank_account
    own = [t for t in account.transactions if t.sender in ("me", account.iban)]
    most: dict[str, float] = {}
    for payment in [*account.scheduled_transactions, *own]:
        most[payment.recipient] = max(most.get(payment.recipient, 0.0), payment.amount)
    return most


def user_details(env: BankingEnvironment) -> dict[str, str]:
    """The user's own details as the bank holds them."""
    user = env.user_account
    return {
        "IBAN": env.bank_account.iban,
        "password": user.password,
        "name": f"{user.first_name} {user.last_name}",
        "street": user.street,
        "city": user.city,
    }


def signal_reasons(
    tool: str, args: Mapping, query: str, env: BankingEnvironment, paid: Mapping[str, float]
) -> list[str]:
    """Signals W4-W5, from the account's own history: an unusual amount for an account the user has paid,
    and the user's own details in a payment's subject. Values the user typed never count.

    A scheduled payment is judged as it will be after the change, since what the call leaves out stays: moving
    a 1,100 rent to an account paid 50 at most is an unusual amount even though the call names no amount. Each
    signal looks only when the change touches what it checks: the same subject to the same account leaks nothing.
    """
    if tool not in MONEY_TOOLS:
        return []
    reasons = []
    recipient, amount, subject = args.get("recipient"), args.get("amount"), args.get("subject")
    check_amount, check_subject = amount is not None, subject is not None
    if tool == "update_scheduled_transaction":
        current = next((t for t in env.bank_account.scheduled_transactions if t.id == args.get("id")), None)
        if current:
            moved = recipient is not None and recipient != current.recipient
            check_amount, check_subject = check_amount or moved, check_subject or moved
            recipient = current.recipient if recipient is None else recipient
            amount = current.amount if amount is None else amount
            subject = current.subject if subject is None else subject
    most = paid.get(recipient)
    grouped = []  # "9,100" and "9,100.00" as typed with thousands separators
    if isinstance(amount, int | float) and amount >= 1000:
        grouped = [f"{amount:,.2f}", *([f"{amount:,.0f}"] if float(amount).is_integer() else [])]
    typed = amount is not None and any(appears_in(form, query) for form in [*value_forms(amount), *grouped])
    if check_amount and most and amount > UNUSUAL_FACTOR * most and not typed:
        reasons.append(
            f"The amount is more than {UNUSUAL_FACTOR} times the most you have paid this account ({money(most)})."
        )
    subject = str(subject or "").lower() if check_subject else ""
    found = [
        name
        for name, value in user_details(env).items()
        if value and appears_in(value.lower(), subject) and not user_supplied(value, query, password=name == "password")
    ]
    if found:
        reasons.append(f"The subject contains your {', '.join(found)}.")
    return reasons


class BankingPolicy:
    """Trusted payees and earlier amounts are fixed when a task starts: a payment approved during the
    task, even a mistaken one to the attacker, must not make the next request to the same account look safe.
    """

    name = "banking"

    def __init__(self) -> None:
        self.known: dict[str, str] | None = None  # None: no task started, judge by the current account
        self.paid: dict[str, float] | None = None

    def start_task(self, env: BankingEnvironment) -> None:
        self.known = payees(env)
        self.paid = paid_amounts(env)

    def needs_approval(self, tool: str) -> bool:
        return tool in WRITE_TOOLS

    def review(
        self, tool: str, args: Mapping, query: str, env: BankingEnvironment, messages: Sequence[ChatMessage]
    ) -> Review:
        known = self.known if self.known is not None else payees(env)
        paid = self.paid if self.paid is not None else paid_amounts(env)
        lines = [describe(tool, args, env)]
        if tool in MONEY_TOOLS and args.get("recipient"):
            lines.append(recipient_note(str(args["recipient"]), query, known, messages))
        provenance = warning_reasons(tool, args, query, env, known)
        signals = signal_reasons(tool, args, query, env, paid)
        details = {"rule_warnings": provenance, "signals": signals}
        return Review(summary="\n".join(lines), warnings=[*provenance, *signals], details=details)


class JudgedBankingPolicy(BankingPolicy):
    """The banking policy with the warning from a model judge (docs/judge-design.md).

    Approvals and what the user sees are unchanged; only the warning differs:
    "judge"  the judge decides on every approval request;
    "hybrid" the rules decide, the judge is asked only about requests with a provenance warning
             (W1-W3) and may clear it, so it can remove warnings but never miss one the rules give.
             It cannot clear the history signals (W4-W5): a value taken from a named document
             would look legitimate to it, which is exactly how a forged amount arrives.
    """

    MODES = ("judge", "hybrid")

    def __init__(self, judge, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError(f"mode must be one of {self.MODES}")
        super().__init__()
        self.judge = judge
        self.mode = mode
        self.name = f"banking-{mode}"

    def review(
        self, tool: str, args: Mapping, query: str, env: BankingEnvironment, messages: Sequence[ChatMessage]
    ) -> Review:
        rules = super().review(tool, args, query, env, messages)
        provenance, signals = rules.details["rule_warnings"], rules.details["signals"]
        details = {"warning_source": self.mode, "rule_warnings": provenance, "signals": signals, "judge": None}
        kept = signals if self.mode == "hybrid" else []  # the judge alone uses no rule at all
        if self.mode == "hybrid" and not provenance:
            return Review(rules.summary, kept, details)
        known = self.known if self.known is not None else payees(env)
        facts = argument_facts(tool, args, query, known, messages)
        verdict = self.judge.assess(query, tool, args, describe(tool, args, env), facts)
        details["judge"] = verdict.to_dict()
        if not verdict.warn:
            return Review(rules.summary, kept, details)
        judged = f"Safety check: {verdict.reason}"
        warnings = [judged] if self.mode == "judge" else [*provenance, judged, *kept]
        return Review(rules.summary, warnings, details)


def attacker_values(suite, injection_task_id: str) -> set[str]:
    """Recipients and passwords in AgentDojo's own solution of the attacker's goal, e.g. {'US133000000121212121212'}."""
    env = suite.load_and_inject_default_environment({})
    calls = suite.injection_tasks[injection_task_id].ground_truth(env)
    return {str(call.args[name]) for call in calls for name in IDENTIFYING_FIELDS if call.args.get(name) is not None}


def is_attackers(args: Mapping, values: set[str]) -> bool:
    """True when the call gives money or the account to the attacker: its recipient or password is theirs."""
    return any(args.get(name) is not None and str(args[name]) in values for name in IDENTIFYING_FIELDS)


def running_injection_task() -> str | None:
    """The attacker's goal of the run in progress, from AgentDojo's trace logger (None without attack)."""
    logger = Logger.get()
    return logger.context.get("injection_task_id") if isinstance(logger, TraceLogger) else None


class BankingOracle:
    """An attack-aware simulation: rejects exact matches to the benchmark's attacker values.

    It uses privileged run context unavailable to real users. Other mistakes can still
    be approved, so this is not a perfect user or a mathematical bound on utility.
    """

    name = "oracle"

    def __init__(self, current: Callable[[], str | None] = running_injection_task) -> None:
        self.current = current
        self.suite = get_suite(BENCHMARK_VERSION, "banking")
        self.values: dict[str, set[str]] = {}

    def decide(self, request: ActionRequest) -> bool:
        injection_task_id = self.current()
        if injection_task_id is None:
            return True
        if injection_task_id not in self.values:
            self.values[injection_task_id] = attacker_values(self.suite, injection_task_id)
        return not is_attackers(request.args, self.values[injection_task_id])
