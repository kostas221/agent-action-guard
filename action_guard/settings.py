"""Experiment settings shared by every script, so runs and reports cannot disagree."""

BENCHMARK_VERSION = "v1.2.2"
PUBLISHED_VERSION = "v1"  # the version AgentDojo's published results table was run on
ATTACK = "important_instructions"  # AgentDojo's strongest baseline attack, used in its results table
DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"  # has published AgentDojo results; cheapest model we have
SUITES = ("workspace", "travel", "banking", "slack")
