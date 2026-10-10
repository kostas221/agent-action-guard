"""A tool declared as reading is never asked about: every checked tools file is held against what the tools do."""

import json
from pathlib import Path

import pytest
from agentdojo.task_suite.load_suites import get_suite

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
