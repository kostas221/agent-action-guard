"""A tool declared as reading is never asked about: every checked tools file is held against what the tools do."""

import json
from pathlib import Path

import pytest
from agentdojo.task_suite.load_suites import get_suite

from action_guard.automatic import AutomaticPolicy, check_tools, load_tools
from classify_tools import declaration_problems


@pytest.mark.parametrize("suite_name", ["banking", "slack"])
def test_every_tool_that_changes_the_environment_is_declared_as_acting(suite_name):
    suite = get_suite("v1.2.2", suite_name)
    checked = json.loads(Path(f"policies/{suite_name}-tools.json").read_text(encoding="utf-8"))
    wrong, unchecked = declaration_problems(checked, suite)
    assert wrong == []
    # tools no solution calls are not checked this way; today only three banking reads, none acting
    assert unchecked == (["get_balance", "get_iban", "get_user_info"] if suite_name == "banking" else [])
    assert all(checked[name]["effect"] == "reads" for name in unchecked)


@pytest.mark.parametrize("model", ["gpt-4o-mini", "gpt-6-luna"])
def test_the_check_catches_the_mistake_both_model_drafts_made(model):
    """Both drafts declared get_webpage as reading; a person corrected it. The check finds it on its own: a visit
    is recorded in the environment, and the address can carry information out."""
    draft = json.loads(Path(f"policies/drafts/slack-tools-{model}.json").read_text(encoding="utf-8"))
    wrong, _ = declaration_problems(draft, get_suite("v1.2.2", "slack"))
    assert wrong == ["get_webpage"]


SLACK = get_suite("v1.2.2", "slack")


def checked_slack() -> dict:
    return json.loads(Path("policies/slack-tools.json").read_text(encoding="utf-8"))


def changed(tool: str, **entry) -> dict:
    tools = checked_slack()
    tools[tool] = entry
    return tools


@pytest.mark.parametrize(
    ("tools", "problem"),
    [
        (changed("send_direct_message", effect="act", roles={"recipient": "target", "body": "content"}), "effect"),
        (changed("send_direct_message", roles={"recipient": "target", "body": "content"}), "effect"),
        (changed("send_direct_message", effect="acts", roles={"recipient": "taget", "body": "content"}), "role"),
        (changed("send_direct_message", effect="acts", roles=["recipient"]), "roles must be"),
        (changed("send_direct_message", effect="acts", roles={}, extra=1), "unknown keys"),
        (changed("read_inbox", effect="reads", roles={}, irreversible=True), "irreversible"),
        (changed("read_inbox", effect="reads", roles={}, high_stakes=True), "high_stakes"),
    ],
)
def test_a_misspelled_or_malformed_entry_is_refused_not_read_as_reading(tools, problem):
    """Review of 0.4: an entry with effect "act" ran its tool without approval. Now nothing runs on such a file."""
    with pytest.raises(ValueError, match=problem):
        AutomaticPolicy("slack", tools)


def test_an_argument_of_an_acting_tool_without_a_role_is_refused():
    """Review of 0.4: a recipient left out of the roles was never checked. Every argument needs a role."""
    tools = changed("send_direct_message", effect="acts", roles={"body": "content"})
    with pytest.raises(ValueError, match=r"send_direct_message: arguments without a role \['recipient'\]"):
        check_tools("slack", tools, SLACK.tools)
    tools = changed(
        "send_direct_message", effect="acts", roles={"recipient": "target", "body": "content", "cc": "target"}
    )
    with pytest.raises(ValueError, match=r"roles for arguments the tool does not have \['cc'\]"):
        check_tools("slack", tools, SLACK.tools)


def test_a_key_given_twice_is_refused(tmp_path):
    text = Path("policies/slack-tools.json").read_text(encoding="utf-8")
    twice = text.replace('"effect": "reads",', '"effect": "acts", "effect": "reads",', 1)
    (tmp_path / "slack-tools.json").write_text(twice, encoding="utf-8")
    with pytest.raises(ValueError, match="given twice"):
        load_tools("slack", str(tmp_path))


@pytest.mark.parametrize("suite_name", ["banking", "slack"])
def test_the_checked_files_pass_every_check(suite_name):
    check_tools(suite_name, load_tools(suite_name), get_suite("v1.2.2", suite_name).tools)
