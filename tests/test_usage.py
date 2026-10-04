from types import SimpleNamespace

import pytest
from agentdojo.logging import OutputLogger, TraceLogger

from action_guard.usage import BudgetExceeded, UsageMeter, cost_usd

MODEL = "gpt-4o-mini-2024-07-18"


def fake_client(prompt_tokens=1000, cached_tokens=0, completion_tokens=100):
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )
    completions = SimpleNamespace(create=lambda **kwargs: SimpleNamespace(usage=usage))
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_cost_uses_cached_and_output_prices():
    assert cost_usd(MODEL, 1_000_000, 0, 0) == pytest.approx(0.15)
    assert cost_usd(MODEL, 1_000_000, 1_000_000, 0) == pytest.approx(0.075)
    assert cost_usd(MODEL, 0, 0, 1_000_000) == pytest.approx(0.60)


def test_meter_adds_usage_to_the_current_run_trace(tmp_path):
    meter = UsageMeter(max_usd=1.0)
    client = meter.wrap_client(fake_client(cached_tokens=400), role="agent")
    with TraceLogger(
        delegate=OutputLogger(str(tmp_path)),
        suite_name="banking",
        user_task_id="user_task_0",
        injection_task_id=None,
        attack_type="none",
        pipeline_name=MODEL,
    ) as logger:
        client.chat.completions.create(model=MODEL, messages=[])
        client.chat.completions.create(model=MODEL, messages=[])
        run_usage = logger.context["usage"]["agent"]
    assert run_usage["calls"] == 2
    assert run_usage["prompt_tokens"] == 2000
    assert run_usage["cached_tokens"] == 800
    assert run_usage["usd"] == pytest.approx(meter.usd)


def test_meter_stops_once_the_budget_is_spent():
    meter = UsageMeter(max_usd=0.0002)
    client = meter.wrap_client(fake_client(), role="agent")
    client.chat.completions.create(model=MODEL, messages=[])  # costs $0.00021
    with pytest.raises(BudgetExceeded):
        client.chat.completions.create(model=MODEL, messages=[])
