#!/usr/bin/env bash
# Recompute every published result file from the run traces, with no model call and no cost (docs/evidence.md).
# Run from the repository root after extracting the trace archive there; then `git status --short results/` is empty.
set -euo pipefail

AUTO="guard-auto-follow-warnings guard-auto-hybrid-follow-warnings guard-auto-hybrid-mini-follow-warnings"
DEFENSES="defense-tool_filter defense-repeat_user_prompt defense-spotlighting_with_delimiting"
SLACK="guard-auto-follow-warnings guard-auto-hybrid-follow-warnings"

uv run python evidence.py

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

# 0.4 (replay and supplied calls; the live runs, once published, are added here)
uv run python fixes_replay.py runs-v0.3-banking runs-v0.3
uv run python failure_matrix.py

uv run python evidence.py
git status --short results/
