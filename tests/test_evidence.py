"""The evidence index (action_guard/evidence.py, evidence.py): a missing planned repeat, changed traces, another
scorer or an edited result file are each found, without a model call."""

import json
import shutil
from pathlib import Path

import evidence
from action_guard.evidence import SCORING_FILES, Planned, scoring_identity, trace_digest

TRACE = {"suite_name": "banking", "user_task_id": "user_task_0", "injection_task_id": None, "utility": True}


def write_trace(root: Path, rep: str, task: str = "user_task_0", body: dict | None = None) -> Path:
    path = root / "runs-x" / "baseline" / rep / "model" / "banking" / task / "none" / "none.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body or {**TRACE, "user_task_id": task}), encoding="utf-8")
    return path


def test_the_digest_covers_every_trace_by_path_and_bytes(tmp_path):
    path = write_trace(tmp_path, "rep1")
    first, count = trace_digest(tmp_path / "runs-x", "baseline", "rep1", "banking")
    assert count == 1
    path.write_text(json.dumps({**TRACE, "utility": False}), encoding="utf-8")
    assert trace_digest(tmp_path / "runs-x", "baseline", "rep1", "banking")[0] != first
    write_trace(tmp_path, "rep1", "user_task_1")
    assert trace_digest(tmp_path / "runs-x", "baseline", "rep1", "banking")[1] == 2


def test_a_planned_repeat_that_is_missing_altogether_is_found(tmp_path, monkeypatch):
    write_trace(tmp_path, "rep1")
    monkeypatch.setattr(evidence, "PLANNED", (Planned("runs-x", ("baseline",), (("banking", 2),)),))
    _, problems, absent = evidence.survey(tmp_path)
    assert absent == []
    assert "runs-x/baseline/rep2 banking: the planned repeat is missing" in problems
    assert any("rep1 banking: 168 of 169 runs missing" in problem for problem in problems)


def test_a_planned_experiment_not_run_yet_is_pending_not_a_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "PLANNED", (Planned("runs-x", ("baseline",), (("banking", 1),), status="planned"),))
    experiments, problems, absent = evidence.survey(tmp_path)
    assert problems == [] and absent == ["runs-x"] and experiments[0]["status"] == "planned"


def test_the_scoring_identity_changes_with_the_reporting_code(tmp_path):
    for name in SCORING_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(name, tmp_path / name)
    before = scoring_identity(tmp_path)
    assert before == scoring_identity(tmp_path)
    (tmp_path / "report.py").write_text((tmp_path / "report.py").read_text(encoding="utf-8") + "\n# a change\n")
    after = scoring_identity(tmp_path)
    assert after["identity"] != before["identity"] and after["files"]["report.py"] != before["files"]["report.py"]


def test_changed_traces_another_execution_another_scorer_and_an_edited_result_are_reported():
    cell = {"config": "baseline", "rep": "rep1", "suite": "banking", "traces": 169, "execution": "abc", "digest": "d1"}
    index = {
        "experiments": [{"runs_dir": "runs-x", "status": "published", "cells": [cell]}],
        "scoring": {"identity": "s1", "files": {"report.py": "r1"}},
        "results": {"results/a.json": "h1", "results/b.json": "h2"},
    }
    now = [{"runs_dir": "runs-x", "status": "published", "cells": [{**cell, "digest": "d2"}]}]
    scoring = {"identity": "s2", "files": {"report.py": "r2"}}
    problems = evidence.compare(index, now, scoring, {"results/a.json": "h9"}, absent=[])
    assert any("traces changed" in p for p in problems)
    assert any("scoring code changed (report.py)" in p for p in problems)
    assert "results/a.json: changed since the index was built" in problems
    assert "results/b.json: in the index, not on disk" in problems
    rerun = [{"runs_dir": "runs-x", "status": "published", "cells": [{**cell, "execution": "xyz"}]}]
    assert any("executed by xyz" in p for p in evidence.compare(index, rerun, index["scoring"], index["results"], []))
    assert evidence.compare(index, now, scoring, {}, absent=["runs-x"])[0].startswith("the scoring code changed")


def test_a_text_file_has_the_same_digest_with_either_line_ending(tmp_path):
    from action_guard.evidence import text_sha256

    (tmp_path / "lf.json").write_bytes(b'{\n "a": 1\n}\n')
    (tmp_path / "crlf.json").write_bytes(b'{\r\n "a": 1\r\n}\r\n')
    assert text_sha256(tmp_path / "lf.json") == text_sha256(tmp_path / "crlf.json")


def test_a_published_result_is_never_changed_but_may_be_written_again_as_it_is(tmp_path, monkeypatch, capsys):
    from action_guard.evidence import write_result

    monkeypatch.chdir(tmp_path)
    out = Path("results") / "v9" / "comparison.json"
    assert write_result(out, '{"a": 1}')
    (tmp_path / "results" / "evidence.json").write_text(json.dumps({"results": {out.as_posix(): "x"}}))
    index = Path("results") / "evidence.json"
    assert write_result(out, '{"a": 1}', index)  # the same content: reproduction
    assert not write_result(out, '{"a": 2}', index)  # a change to a published table
    assert out.read_text(encoding="utf-8") == '{"a": 1}' and "Not saved" in capsys.readouterr().out
    assert write_result(Path("results") / "v9" / "new.json", '{"b": 1}', index)  # not published yet
