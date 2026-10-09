"""One repeat is one experiment: resumed only with the same setup and run files, and never pooled with another."""

import json
import shutil
from pathlib import Path

import pytest

from action_guard.manifest import LEGACY, RUN_FILES, ExperimentMismatch, check_or_write, identity, label
from action_guard.metrics import experiment_problems, load_runs, setup_problems
from action_guard.settings import DEFAULT_MODEL, JUDGE_MODEL, PLANNER_MODEL

HYBRID = "guard-auto-hybrid-follow-warnings"


@pytest.fixture
def root(tmp_path):
    """A copy of the files a run executes, so they can be changed without touching the project."""
    root = tmp_path / "project"
    for path in [*RUN_FILES, *(p.as_posix() for p in Path("policies").glob("*.json"))]:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, root / path)
    (root / "README.md").write_text("notes", encoding="utf-8")
    return root


def test_a_repeat_is_resumed_only_with_the_same_run_files(tmp_path, root):
    logdir = tmp_path / "runs" / HYBRID / "rep1"
    first = check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)
    assert check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)["fingerprint"] == first["fingerprint"]
    (root / "README.md").write_text("notes, corrected", encoding="utf-8")  # not a run file
    check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)
    with (root / "action_guard" / "judge.py").open("a", encoding="utf-8") as judge:
        judge.write("\n# a change\n")
    with pytest.raises(ExperimentMismatch, match="action_guard/judge.py"):
        check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)


def test_a_change_of_tools_file_or_settings_is_another_experiment(tmp_path, root):
    logdir = tmp_path / "runs" / HYBRID / "rep1"
    check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)
    with pytest.raises(ExperimentMismatch, match="agent_model"):
        check_or_write(logdir, HYBRID, "gpt-6-luna", root)
    tools = root / "policies" / "slack-tools.json"
    tools.write_text(tools.read_text(encoding="utf-8").replace('"target"', '"content"', 1), encoding="utf-8")
    with pytest.raises(ExperimentMismatch, match="policies/slack-tools.json"):
        check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)


def test_runs_made_before_manifests_stay_legacy_and_are_not_resumed(tmp_path, root):
    logdir = tmp_path / "runs" / HYBRID / "rep1"
    trace = logdir / DEFAULT_MODEL / "banking" / "user_task_0" / "none" / "none.json"
    trace.parent.mkdir(parents=True)
    trace.write_text("{}", encoding="utf-8")
    assert label(logdir) == LEGACY
    with pytest.raises(ExperimentMismatch, match="before run manifests"):
        check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)


def test_the_identity_names_every_model_in_the_configuration():
    hybrid = identity(HYBRID, DEFAULT_MODEL)
    assert (hybrid["planner_model"], hybrid["judge_model"]) == (PLANNER_MODEL, JUDGE_MODEL)
    mini = identity("guard-auto-hybrid-mini-follow-warnings", DEFAULT_MODEL)
    assert mini["planner_model"] == DEFAULT_MODEL and mini["judge_votes"] == 3
    rules = identity("guard-auto-follow-warnings", DEFAULT_MODEL)
    assert rules["judge_model"] is None and rules["planner_model"] == PLANNER_MODEL
    assert identity("defense-tool_filter", DEFAULT_MODEL)["agentdojo_defense"] == "tool_filter"


def write(runs, rep, model, version="v1.2.2"):
    path = runs / "baseline" / rep / model / "banking" / "user_task_0" / "none" / "none.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    trace = {"suite_name": "banking", "pipeline_name": model, "user_task_id": "user_task_0", "injection_task_id": None}
    trace |= {"attack_type": None, "utility": True, "security": True, "error": None, "duration": 1.0, "usage": {}}
    trace |= {"benchmark_version": version, "agentdojo_package_version": "0.1.35"}
    path.write_text(json.dumps(trace), encoding="utf-8")


def test_runs_of_two_models_or_two_benchmark_versions_are_not_pooled(tmp_path):
    write(tmp_path, "rep1", DEFAULT_MODEL)
    assert experiment_problems(load_runs(tmp_path, "baseline")) == []
    write(tmp_path, "rep2", "gpt-4o-2024-05-13", version="v1.2")
    problems = experiment_problems(load_runs(tmp_path, "baseline"))
    assert any("pipeline_name" in p for p in problems) and any("benchmark_version" in p for p in problems)


def test_repeats_of_different_experiments_are_not_pooled(tmp_path):
    write(tmp_path, "rep1", DEFAULT_MODEL)
    write(tmp_path, "rep2", DEFAULT_MODEL)
    (tmp_path / "baseline" / "rep2" / "manifest.json").write_text(json.dumps({"fingerprint": "f" * 64}))
    problems = experiment_problems(load_runs(tmp_path, "baseline"))
    assert problems == ["repeats from different experiments: rep1 legacy, rep2 ffffffffffff"]


def row(model, version="v1.2.2"):
    return {"pipeline_name": model, "benchmark_version": version, "agentdojo_package_version": "0.1.35"}


def test_configurations_share_a_table_only_with_the_same_agent_and_benchmark():
    same = {"baseline": [row(DEFAULT_MODEL)], "defense-tool_filter": [row(f"{DEFAULT_MODEL}-tool_filter")]}
    assert setup_problems(same) == []  # a defense's pipeline is named <model>-<defense>
    assert setup_problems({**same, HYBRID: [row("gpt-6-luna")]})[0].startswith("different setups")
    problems = setup_problems({"baseline": [row(DEFAULT_MODEL, "v1")], HYBRID: [row(DEFAULT_MODEL, "v1")]})
    assert problems == ["benchmark v1, but tasks are scored with v1.2.2"]


def test_another_attack_is_another_experiment(tmp_path, root):
    logdir = tmp_path / "runs" / HYBRID / "rep1"
    assert check_or_write(logdir, HYBRID, DEFAULT_MODEL, root)["identity"]["attack"] == "important_instructions"
    with pytest.raises(ExperimentMismatch, match="attack: 'important_instructions' -> 'tool_knowledge'"):
        check_or_write(logdir, HYBRID, DEFAULT_MODEL, root, attack="tool_knowledge")


def test_a_repeat_run_under_another_attack_is_read_only_under_that_attack(tmp_path):
    write(tmp_path, "rep1", DEFAULT_MODEL)  # its clean run would be kept, and every attacked run skipped
    manifest = {"fingerprint": "f" * 64, "identity": {"attack": "tool_knowledge"}}
    (tmp_path / "baseline" / "rep1" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="pass --attack tool_knowledge"):
        load_runs(tmp_path, "baseline")
    assert len(load_runs(tmp_path, "baseline", "tool_knowledge")) == 1
