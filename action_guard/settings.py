"""Experiment settings shared by every script, so runs and reports cannot disagree."""

BENCHMARK_VERSION = "v1.2.2"
PUBLISHED_VERSION = "v1"  # the version AgentDojo's published results table was run on
ATTACK = "important_instructions"  # AgentDojo's strongest baseline attack, used in its results table
# The second template, chosen before any run with it (docs/automatic-policy.md, the frozen protocol): the same
# message with the attacker's exact calls and arguments written into it. Its runs go to their own runs dir.
ATTACKS = (ATTACK, "tool_knowledge")
DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"  # has published AgentDojo results; cheapest model we have
JUDGE_MODEL = "gpt-4o-mini-2024-07-18"  # the judge of the 0.2 warnings (docs/judge-design.md)
# Majority of up to three calls for the hybrid, adopted after the live runs (which used one call) by the rule
# stated before the offline replay, results/judge-pilot-3.json. The judge alone keeps one call.
HYBRID_JUDGE_VOTES = 3
# 0.3: the model that plans from the request (A1, docs/automatic-policy.md): chosen after the offline replay,
# where it listed the right actions for 15 of 16 banking tasks, as the large model did, at a twelfth of its price
PLANNER_MODEL = "gpt-6-luna"
SUITES = ("workspace", "travel", "banking", "slack")
