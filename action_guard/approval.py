"""The approval gate: an action that changes something runs only after the user approved that exact action, once.

The gate knows nothing about AgentDojo or banking. A request is created for one call
(tool name and arguments), the user approves or rejects it, and before execution the gate
looks for an approved, unused request for exactly that call:

    no request, a pending or a rejected one   -> refused   (no bypass)
    arguments differ from the approved ones   -> refused   (changed after approval)
    the approval was already used             -> refused   (one approval, one execution)

Approvers decide on requests. Real users click; the simulated users of
docs/approval-policy.md are below, so every decision goes through the same gate.
"""

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Status(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONSUMED = "consumed"  # approval used; execution has not returned yet
    EXECUTED = "executed"
    FAILED = "failed"


def fingerprint(tool: str, args: Mapping) -> str:
    """Identifies one exact call: any change to the tool or to any argument changes it; argument order does not."""
    canonical = json.dumps({"tool": tool, "args": dict(args)}, sort_keys=True, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ActionRequest:
    id: int
    tool: str
    args: dict  # a private copy: what the user saw is what gets checked
    fingerprint: str
    summary: str = ""  # what the user is shown
    warnings: list[str] = field(default_factory=list)
    status: Status = Status.PENDING
    error: str | None = None
    details: dict = field(default_factory=dict)  # how the warning was reached (rules, judge verdict)


class ApprovalGate:
    def __init__(self) -> None:
        self.requests: list[ActionRequest] = []

    def request(
        self,
        tool: str,
        args: Mapping,
        summary: str = "",
        warnings: list[str] | None = None,
        details: Mapping | None = None,
    ) -> ActionRequest:
        """Register a call that waits for a decision."""
        args = copy.deepcopy(dict(args))
        request = ActionRequest(
            id=len(self.requests) + 1,
            tool=tool,
            args=args,
            fingerprint=fingerprint(tool, args),
            summary=summary,
            warnings=list(warnings or []),
            details=copy.deepcopy(dict(details or {})),
        )
        self.requests.append(request)
        return request

    def decide(self, request_id: int, approve: bool) -> None:
        """Record the user's decision; it is final."""
        request = self.requests[request_id - 1]
        if request.status is not Status.PENDING:
            raise ValueError(f"Request {request_id} is already {request.status.value}")
        request.status = Status.APPROVED if approve else Status.REJECTED

    def consume(self, tool: str, args: Mapping) -> ActionRequest | None:
        """Consume an approval once, before execution. Its eventual outcome is recorded by finish()."""
        wanted = fingerprint(tool, args)
        for request in self.requests:
            if request.status is Status.APPROVED and request.fingerprint == wanted:
                request.status = Status.CONSUMED
                return request
        return None

    def finish(self, request_id: int, error: str | None = None) -> None:
        """Record a consumed approval's outcome; failure never makes the approval reusable."""
        request = self.requests[request_id - 1]
        if request.status is not Status.CONSUMED:
            raise ValueError(f"Request {request_id} is not awaiting an execution result")
        request.error = error
        request.status = Status.EXECUTED if error is None else Status.FAILED


class Approver(Protocol):
    name: str

    def decide(self, request: ActionRequest) -> bool: ...


class ApproveAll:
    """The inattentive user: says yes to everything."""

    name = "approve-all"

    def decide(self, request: ActionRequest) -> bool:
        return True


class RejectAll:
    """The most cautious user: says no to everything."""

    name = "reject-all"

    def decide(self, request: ActionRequest) -> bool:
        return False


class FollowWarnings:
    """Says yes unless the request carries a warning."""

    name = "follow-warnings"

    def decide(self, request: ActionRequest) -> bool:
        return not request.warnings


class ConsoleApprover:
    """A real person at the terminal.

    A warned request cannot be approved with a reflex "y": it needs the word "approve"
    (friction where the risk is, as banks do). Every answer is echoed back, so a slip
    shows at once; anything else, including Enter, rejects.
    """

    name = "console"

    def __init__(self, ask=input, show=print) -> None:
        self.ask = ask
        self.show = show

    def decide(self, request: ActionRequest) -> bool:
        self.show(f"\n=== Approval needed (request {request.id}) ===")
        for warning in request.warnings:
            self.show(f"  [!] WARNING: {warning}")
        self.show(request.summary)
        arguments = json.dumps(request.args, sort_keys=True, ensure_ascii=True, allow_nan=False)
        self.show(f"Exact call: {request.tool}({arguments})")
        if request.warnings:
            approved = self.ask("Type 'approve' to approve anyway, or Enter to reject: ").strip().lower() == "approve"
        else:
            approved = self.ask("Approve? [y/N] ").strip().lower() in ("y", "yes")
        self.show("  -> APPROVED" if approved else "  -> REJECTED")
        return approved
