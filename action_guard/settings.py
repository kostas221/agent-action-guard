"""Experiment settings shared by every script, so runs and reports cannot disagree."""

BENCHMARK_VERSION = "v1.2.2"
PUBLISHED_VERSION = "v1"  # the version AgentDojo's published results table was run on
ATTACK = "important_instructions"  # AgentDojo's strongest baseline attack, used in its results table
DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"  # has published AgentDojo results; cheapest model we have
JUDGE_MODEL = "gpt-4o-mini-2024-07-18"  # the judge of the 0.2 warnings (docs/judge-design.md)
# Majority of up to three calls for the hybrid, adopted after the live runs (which used one call) by the rule
# stated before the offline replay, results/judge-pilot-3.json. The judge alone keeps one call.
HYBRID_JUDGE_VOTES = 3
SUITES = ("workspace", "travel", "banking", "slack")
