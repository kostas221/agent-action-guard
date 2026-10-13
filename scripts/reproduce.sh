#!/usr/bin/env bash
# Recompute every published result file from the run traces, with no model call and no cost (docs/evidence.md).
# Run from the repository root after extracting the trace archive there; then `git status --short results/` is empty.
set -euo pipefail

AUTO="guard-auto-follow-warnings guard-auto-hybrid-follow-warnings guard-auto-hybrid-mini-follow-warnings"
DEFENSES="defense-tool_filter defense-repeat_user_prompt defense-spotlighting_with_delimiting"
SLACK="guard-auto-follow-warnings guard-auto-hybrid-follow-warnings"

# After a change to the scoring code the first check reports that change; this script is how it is tested (every
# result file must come out the same), so it goes on. The last check decides.
uv run python evidence.py || echo "(evidence.py reported the problems above; reproducing the results anyway)"

# 0.1 and 0.2 (banking, hand-written policy)
for c in baseline guard-approve-all guard-follow-warnings guard-oracle guard-reject-all; do uv run python report.py --config $c; done
for c in guard-follow-warnings guard-judge-follow-warnings guard-hybrid-follow-warnings; do uv run python report.py --config $c --runs-dir runs-v0.2; done
for c in guard-follow-warnings guard-hybrid-follow-warnings; do uv run python report.py --config $c --runs-dir runs-v0.2.0; done
uv run python compare.py
uv run python compare.py --runs-dir runs-v0.2 --configs guard-follow-warnings guard-judge-follow-warnings guard-hybrid-follow-warnings
uv run python compare.py --runs-dir runs-v0.2.0 --configs guard-follow-warnings guard-hybrid-follow-warnings
uv run python signals_replay.py

# 0.3 (automatic policy): Slack, banking with AgentDojo's defenses, the second attack template
for c in $SLACK; do uv run python report.py --config $c --runs-dir runs-v0.3; done
for c in $AUTO $DEFENSES; do uv run python report.py --config $c --runs-dir runs-v0.3-banking; done
for c in baseline $AUTO; do uv run python report.py --config $c --runs-dir runs-v0.3-tool-knowledge --attack tool_knowledge; done
uv run python compare.py --runs-dir runs-v0.3 --suite slack --configs $SLACK
uv run python compare.py --runs-dir runs-v0.3-banking --configs $AUTO $DEFENSES
uv run python compare.py --runs-dir runs-v0.3-tool-knowledge --attack tool_knowledge --configs $AUTO
uv run python bootstrap.py --runs-dir runs-v0.3 --suite slack --configs $SLACK
uv run python bootstrap.py --runs-dir runs-v0.3-banking --configs $AUTO $DEFENSES
uv run python bootstrap.py --runs-dir runs-v0.3-banking --reference defense-tool_filter --configs $AUTO
uv run python bootstrap.py --runs-dir runs-v0.3-tool-knowledge --attack tool_knowledge --configs $AUTO

# 0.4: replay, then the live banking runs. results/v0.4/failure-matrix.json was made by the 0.4 policy: it reproduces
# at that code (tag v0.4.0, commit 71d9361: `uv run python failure_matrix.py` there); today's matrix is 0.5's, below
uv run python fixes_replay.py runs-v0.3-banking runs-v0.3
for c in $AUTO; do uv run python report.py --config $c --runs-dir runs-v0.4; done
uv run python compare.py --runs-dir runs-v0.4 --configs $AUTO
uv run python bootstrap.py --runs-dir runs-v0.4 --configs $AUTO

# 0.5 (no new live runs): upper bounds that do not count repeats as independent, which part of the policy stopped
# each request, the 0.5 policy replayed on every stored request, and its failure matrix
uv run python bounds.py --runs-dir runs-v0.4
uv run python bounds.py --runs-dir runs-v0.3-banking
uv run python bounds.py --runs-dir runs-v0.3-tool-knowledge --attack tool_knowledge
uv run python bounds.py --runs-dir runs-v0.3 --suite slack
uv run python barriers.py --runs-dir runs-v0.4
uv run python barriers.py --runs-dir runs-v0.3-banking
uv run python barriers.py --runs-dir runs-v0.3 --suite slack
uv run python policy_replay.py runs-v0.4 --out results/v0.5/replay-runs-v0.4.json
uv run python policy_replay.py runs-v0.3-banking runs-v0.3 --key-roles target,secret --out results/v0.5/replay-runs-v0.3.json
uv run python failure_matrix.py

git status --short results/
uv run python evidence.py
