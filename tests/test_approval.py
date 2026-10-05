import pytest

from action_guard.approval import (
    ApprovalGate,
    ApproveAll,
    ConsoleApprover,
    FollowWarnings,
    RejectAll,
    Status,
    fingerprint,
)

PAYMENT = {"recipient": "GB29NWBK60161331926819", "amount": 10.0, "subject": "Refund", "date": "2022-04-01"}


def approved(gate, tool="send_money", args=PAYMENT):
    request = gate.request(tool, args)
    gate.decide(request.id, approve=True)
    return request


def test_nothing_runs_without_a_request():
    assert ApprovalGate().consume("send_money", PAYMENT) is None


def test_a_pending_request_does_not_run():
    gate = ApprovalGate()
    gate.request("send_money", PAYMENT)
    assert gate.consume("send_money", PAYMENT) is None


def test_a_rejected_request_does_not_run():
    gate = ApprovalGate()
    request = gate.request("send_money", PAYMENT)
    gate.decide(request.id, approve=False)
    assert gate.consume("send_money", PAYMENT) is None
    assert request.status is Status.REJECTED


def test_an_approved_request_runs_once():
    gate = ApprovalGate()
    request = approved(gate)
    assert gate.consume("send_money", PAYMENT) is request
    assert request.status is Status.EXECUTED
    assert gate.consume("send_money", PAYMENT) is None  # replaying the same approval


@pytest.mark.parametrize(
    "change",
    [{"amount": 1000.0}, {"recipient": "US133000000121212121212"}, {"subject": "iPhone 3GS"}, {"date": "2022-04-02"}],
)
def test_changing_any_argument_after_approval_is_refused(change):
    gate = ApprovalGate()
    request = approved(gate)
    assert gate.consume("send_money", {**PAYMENT, **change}) is None
    assert request.status is Status.APPROVED  # the approval still covers only the original call


def test_the_same_arguments_for_another_tool_are_refused():
    gate = ApprovalGate()
    approved(gate, tool="schedule_transaction")
    assert gate.consume("send_money", PAYMENT) is None


def test_editing_the_arguments_after_the_request_does_not_change_what_was_approved():
    gate = ApprovalGate()
    args = dict(PAYMENT)
    request = approved(gate, args=args)
    args["recipient"] = "US133000000121212121212"  # the caller's dict changes after the user said yes
    assert request.args == PAYMENT
    assert gate.consume("send_money", args) is None


def test_two_approvals_allow_two_executions_and_no_more():
    gate = ApprovalGate()
    approved(gate)
    approved(gate)
    assert gate.consume("send_money", PAYMENT) is not None
    assert gate.consume("send_money", PAYMENT) is not None
    assert gate.consume("send_money", PAYMENT) is None


def test_a_decision_is_final():
    gate = ApprovalGate()
    request = gate.request("send_money", PAYMENT)
    gate.decide(request.id, approve=False)
    with pytest.raises(ValueError):
        gate.decide(request.id, approve=True)


def test_argument_order_does_not_change_the_fingerprint():
    assert fingerprint("send_money", {"a": 1, "b": 2}) == fingerprint("send_money", {"b": 2, "a": 1})


def console(answer: str, shown: list) -> ConsoleApprover:
    return ConsoleApprover(ask=lambda _: answer, show=shown.append)


def test_the_console_user_approves_a_plain_request_with_y():
    shown = []
    request = ApprovalGate().request("send_money", PAYMENT, summary="Send 10.00 to GB29")
    assert console("y", shown).decide(request) is True
    assert console("", shown).decide(request) is False  # Enter means no
    assert console("υ", shown).decide(request) is False  # the y key on a Greek keyboard: not a yes
    assert shown.count("Send 10.00 to GB29") == 3
    assert shown.count("  -> APPROVED") == 1 and shown.count("  -> REJECTED") == 2


def test_a_warned_request_needs_the_word_approve_not_a_reflex_y():
    shown = []
    request = ApprovalGate().request("send_money", PAYMENT, summary="Send 10.00 to US133", warnings=["Not typed."])
    assert console("y", shown).decide(request) is False
    assert console("approve", shown).decide(request) is True
    assert shown[1] == "  [!] WARNING: Not typed."  # the warning comes before the summary


def test_simulated_users():
    gate = ApprovalGate()
    plain = gate.request("send_money", PAYMENT)
    flagged = gate.request("send_money", PAYMENT, warnings=["recipient not from the user"])
    assert [ApproveAll().decide(plain), ApproveAll().decide(flagged)] == [True, True]
    assert [RejectAll().decide(plain), RejectAll().decide(flagged)] == [False, False]
    assert [FollowWarnings().decide(plain), FollowWarnings().decide(flagged)] == [True, False]
