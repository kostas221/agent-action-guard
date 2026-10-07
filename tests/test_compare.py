import json
import sys

import compare

SAVED = {"guard-follow-warnings": {"kept": True}}


def saved_results(tmp_path):
    out = tmp_path / "results" / "comparison.json"
    out.parent.mkdir()
    out.write_text(json.dumps(SAVED), encoding="utf-8")
    return out


def test_a_fresh_clone_without_runs_leaves_the_saved_results_alone(tmp_path, monkeypatch):
    out = saved_results(tmp_path)
    monkeypatch.chdir(tmp_path)  # no runs/ here, as in a fresh clone: traces are not in the repository
    monkeypatch.setattr(sys, "argv", ["compare.py"])
    assert compare.main() == 1
    assert json.loads(out.read_text(encoding="utf-8")) == SAVED


def test_a_partial_comparison_is_never_saved(tmp_path):
    out = saved_results(tmp_path)
    partial = {"baseline": {}, "guard-follow-warnings": {}}
    assert not compare.save(partial, ["baseline", "guard-follow-warnings", "guard-hybrid-follow-warnings"], out)
    assert json.loads(out.read_text(encoding="utf-8")) == SAVED
    assert compare.save(partial, ["baseline", "guard-follow-warnings"], out)
    assert json.loads(out.read_text(encoding="utf-8")) == partial
