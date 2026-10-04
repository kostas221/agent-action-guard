"""Token and cost accounting, stored per run inside AgentDojo's own trace files.

Every chat completion goes through a wrapped OpenAI client. The tokens are added
to the trace of the run being executed (key "usage", split by role: "agent" for
the agent's own LLM, "guard" for a guard's LLM in later phases), so the cost of
any configuration can be recomputed from the saved runs alone.
"""

import openai
from agentdojo.logging import Logger, TraceLogger

# USD per 1M tokens: (input, cached input, output). List prices; check them before big runs.
PRICES = {
    "gpt-4o-mini-2024-07-18": (0.15, 0.075, 0.60),
}


class BudgetExceeded(BaseException):
    """BaseException, so AgentDojo's per-task error handling cannot swallow it."""


def cost_usd(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    price_input, price_cached, price_output = PRICES[model]
    fresh = prompt_tokens - cached_tokens
    return (fresh * price_input + cached_tokens * price_cached + completion_tokens * price_output) / 1e6


def empty_usage() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "usd": 0.0}


class UsageMeter:
    """Counts tokens of wrapped clients, records them in the current run's trace and enforces a budget."""

    def __init__(self, max_usd: float):
        self.max_usd = max_usd
        self.usd = 0.0
        self.calls = 0

    def wrap_client(self, client: openai.OpenAI, role: str) -> openai.OpenAI:
        create = client.chat.completions.create

        def counted_create(*args, **kwargs):
            if self.usd >= self.max_usd:
                raise BudgetExceeded(f"stopped at ${self.usd:.4f} (limit ${self.max_usd})")
            completion = create(*args, **kwargs)
            if completion.usage is not None:
                self.record(role, kwargs["model"], completion.usage)
            return completion

        client.chat.completions.create = counted_create
        return client

    def record(self, role: str, model: str, usage) -> None:
        details = usage.prompt_tokens_details
        cached = (details.cached_tokens or 0) if details else 0
        usd = cost_usd(model, usage.prompt_tokens, cached, usage.completion_tokens)
        self.usd += usd
        self.calls += 1

        logger = Logger.get()
        if not isinstance(logger, TraceLogger):
            return
        run_usage = logger.context.setdefault("usage", {}).setdefault(role, empty_usage())
        run_usage["calls"] += 1
        run_usage["prompt_tokens"] += usage.prompt_tokens
        run_usage["cached_tokens"] += cached
        run_usage["completion_tokens"] += usage.completion_tokens
        run_usage["usd"] += usd
