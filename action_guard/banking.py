"""Banking rules of the guard (docs/approval-policy.md): which calls need approval, what the user sees, when to warn.

Trusted: the user's message and the structured fields of the account. A known payee is
an account the user has paid (a past payment or a scheduled one). Someone who only sent
the user money is not trusted: otherwise an attacker becomes "known" by sending one cent
first. Free text the agent read (files, subjects of incoming transactions) is untrusted,
since injections hide there: it never makes a value trusted and is never shown as a label.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from agentdojo.default_suites.v1.banking.task_suite import BankingEnvironment
from agentdojo.logging import Logger, TraceLogger
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import ChatMessage, get_text_content_as_str

from action_guard.approval import ActionRequest
from action_guard.settings import BENCHMARK_VERSION

MONEY_TOOLS = ("send_money", "schedule_transaction", "update_scheduled_transaction")
WRITE_TOOLS = frozenset({*MONEY_TOOLS, "update_password", "update_user_info"})
USER_FIELDS = ("first_name", "last_name", "street", "city")
SCHEDULE_FIELDS = ("recipient", "amount", "subject", "date", "recurring")
IDENTIFYING_FIELDS = ("recipient", "password")  # what makes an action the attacker's: who gets it
LABEL_WIDTH = 40


@dataclass
class Review:
    summary: str  # what the user is shown
    warnings: list[str] = field(default_factory=list)


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


def first_seen(value: str, messages: Sequence[ChatMessage]) -> str | None:
    """The tool call whose output first contained `value`, e.g. read_file('bill-december-2023.txt')."""
    for message in messages:
        if message["role"] == "tool" and value in get_text_content_as_str(message["content"] or []):
            call = message["tool_call"]
            return f"{call.function}({', '.join(repr(v) for v in call.args.values())})"
    return None


def recipient_note(recipient: str, query: str, known: Mapping[str, str], messages: Sequence[ChatMessage]) -> str:
    if recipient in known:
        return f"{recipient}: you have paid them before: {known[recipient]}"
    if recipient in query:
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
    if tool in MONEY_TOOLS and recipient and str(recipient) not in query and str(recipient) not in known:
        reasons.append("The recipient is not in your message and you have never paid them.")
    if tool == "update_password" and str(args.get("password")) not in query:
        reasons.append("You did not type this password.")
    if tool == "update_user_info":
        user = env.user_account
        untyped = [
            name.replace("_", " ")
            for name in USER_FIELDS
            if args.get(name) and args[name] != getattr(user, name) and str(args[name]) not in query
        ]
        if untyped:
            reasons.append(f"You did not type the new {', '.join(untyped)}.")
    return reasons


class BankingPolicy:
    """Trusted payees are fixed when a task starts: a payment approved during the task, even a
    mistaken one to the attacker, must not make the next request to the same account look safe.
    """

    name = "banking"

    def __init__(self) -> None:
        self.known: dict[str, str] | None = None  # None: no task started, judge by the current account

    def start_task(self, env: BankingEnvironment) -> None:
        self.known = payees(env)

    def needs_approval(self, tool: str) -> bool:
        return tool in WRITE_TOOLS

    def review(
        self, tool: str, args: Mapping, query: str, env: BankingEnvironment, messages: Sequence[ChatMessage]
    ) -> Review:
        known = self.known if self.known is not None else payees(env)
        lines = [describe(tool, args, env)]
        if tool in MONEY_TOOLS and args.get("recipient"):
            lines.append(recipient_note(str(args["recipient"]), query, known, messages))
        return Review(summary="\n".join(lines), warnings=warning_reasons(tool, args, query, env, known))


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
    """The best a careful user can do: rejects exactly the attacker's own actions and approves the rest.

    It knows the attack from the benchmark's run context, which a real user never sees, so
    its numbers are an upper bound on what the guard plus a person can achieve.
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
