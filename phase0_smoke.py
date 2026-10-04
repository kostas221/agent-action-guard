"""Phase 0 smoke run: a few AgentDojo banking tasks with gpt-4o-mini and no defense.

Measures utility, attack success and the real token cost per run, so the full
baseline (Phase 1) can be budgeted before money is spent on it.

    uv run python phase0_smoke.py                    # 4 user tasks x 4 injection tasks
    uv run python phase0_smoke.py --max-usd 0.25     # hard stop on spend

Runs already saved under runs/ are skipped on a re-run (no double spend).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import openai
from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
from agentdojo.attacks.attack_registry import load_attack
from agentdojo.benchmark import benchmark_suite_with_injections, benchmark_suite_without_injections
from agentdojo.logging import OutputLogger
from agentdojo.task_suite.load_suites import get_suite, get_suites
from dotenv import load_dotenv

MODEL = "gpt-4o-mini-2024-07-18"
BENCHMARK_VERSION = "v1.2.2"
SUITE = "banking"
ATTACK = "important_instructions"
# USD per 1M tokens for gpt-4o-mini (input, cached input, output); check the price list before big runs
PRICE_INPUT, PRICE_CACHED, PRICE_OUTPUT = 0.15, 0.075, 0.60
# Published AgentDojo run of this model on banking (repo runs/ folder, older benchmark version)
PUBLISHED = {"utility_no_attack": (8, 16), "utility_under_attack": (55, 144), "attack_success": (49, 144)}


class BudgetExceeded(BaseException):
    """BaseException, so AgentDojo's per-task error handling cannot swallow it."""


class UsageMeter:
    def __init__(self, max_usd: float):
        self.max_usd = max_usd
        self.runs = self.calls = self.prompt = self.cached = self.completion = 0

    @property
    def usd(self) -> float:
        fresh = self.prompt - self.cached
        return (fresh * PRICE_INPUT + self.cached * PRICE_CACHED + self.completion * PRICE_OUTPUT) / 1e6

    def wrap_client(self, client: openai.OpenAI) -> openai.OpenAI:
        create = client.chat.completions.create

        def counted_create(*args, **kwargs):
            if self.usd >= self.max_usd:
                raise BudgetExceeded(f"stopped at ${self.usd:.4f} (limit ${self.max_usd})")
            completion = create(*args, **kwargs)
            if completion.usage is not None:
                self.calls += 1
                self.prompt += completion.usage.prompt_tokens
                self.completion += completion.usage.completion_tokens
                details = completion.usage.prompt_tokens_details
                self.cached += (details.cached_tokens or 0) if details else 0
            return completion

        client.chat.completions.create = counted_create
        return client

    def wrap_pipeline(self, pipeline: AgentPipeline) -> AgentPipeline:
        query = pipeline.query

        def counted_query(*args, **kwargs):
            self.runs += 1
            return query(*args, **kwargs)

        pipeline.query = counted_query
        return pipeline


def by_number(task_ids) -> list[str]:
    return sorted(task_ids, key=lambda task_id: int(task_id.rsplit("_", 1)[1]))


def runs_needed(suite) -> int:
    """Runs of a full suite: user tasks clean + every (user, injection) pair + injection tasks alone."""
    n_user, n_inj = len(suite.user_tasks), len(suite.injection_tasks)
    return n_user + n_user * n_inj + n_inj


def rate(results: dict) -> tuple[int, int]:
    return sum(results.values()), len(results)


def pct(pair: tuple[int, int]) -> str:
    hits, total = pair
    return f"{hits}/{total} ({100 * hits / total:.0f}%)" if total else "-"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-tasks", type=int, default=4, help="first N user tasks of the suite")
    ap.add_argument("--injection-tasks", type=int, default=4, help="first N injection tasks of the suite")
    ap.add_argument("--max-usd", type=float, default=0.5)
    ap.add_argument("--logdir", default="runs")
    args = ap.parse_args()

    load_dotenv(".env")
    meter = UsageMeter(args.max_usd)
    llm = OpenAILLM(meter.wrap_client(openai.OpenAI()), MODEL)
    llm.name = MODEL  # the attack addresses the model by name, looked up from the pipeline name
    pipeline = AgentPipeline.from_config(
        PipelineConfig(llm=llm, model_id=None, defense=None, system_message_name=None, system_message=None)
    )
    meter.wrap_pipeline(pipeline)

    suite = get_suite(BENCHMARK_VERSION, SUITE)
    user_tasks = by_number(suite.user_tasks)[: args.user_tasks]
    injection_tasks = by_number(suite.injection_tasks)[: args.injection_tasks]
    logdir = Path(args.logdir)
    print(f"{MODEL} | {SUITE} {BENCHMARK_VERSION} | {len(user_tasks)} user x {len(injection_tasks)} injection tasks")

    started = time.time()
    try:
        with OutputLogger(str(logdir)):
            clean = benchmark_suite_without_injections(
                pipeline,
                suite,
                logdir=logdir,
                force_rerun=False,
                user_tasks=user_tasks,
                benchmark_version=BENCHMARK_VERSION,
            )
            attack = load_attack(ATTACK, suite, pipeline)
            attacked = benchmark_suite_with_injections(
                pipeline,
                suite,
                attack,
                logdir=logdir,
                force_rerun=False,
                user_tasks=user_tasks,
                injection_tasks=injection_tasks,
                verbose=False,
                benchmark_version=BENCHMARK_VERSION,
            )
    except BudgetExceeded as e:
        print(f"!! {e}. Results so far are saved in {logdir}/ and are skipped on a re-run.")
        return 1
    minutes = (time.time() - started) / 60

    ours = {
        "utility_no_attack": rate(clean["utility_results"]),
        "utility_under_attack": rate(attacked["utility_results"]),
        "attack_success": rate(attacked["security_results"]),
        "injection_goals_doable": rate(attacked["injection_tasks_utility_results"]),
    }
    print("\n                        this run (subset)      published (full banking, older version)")
    for key, label in (
        ("utility_no_attack", "task done, no attack"),
        ("utility_under_attack", "task done, under attack"),
        ("attack_success", "attack succeeded"),
    ):
        print(f"  {label:24}{pct(ours[key]):23}{pct(PUBLISHED[key])}")
    print(
        f"  {'attacker goal doable':24}{pct(ours['injection_goals_doable'])}  (injection tasks run as normal requests)"
    )

    print(f"\nRuns executed now: {meter.runs} | LLM calls: {meter.calls} | {minutes:.1f} min")
    print(f"Tokens: {meter.prompt:,} in ({meter.cached:,} cached), {meter.completion:,} out | cost ${meter.usd:.4f}")
    summary = {
        "model": MODEL,
        "suite": SUITE,
        "benchmark_version": BENCHMARK_VERSION,
        "attack": ATTACK,
        "user_tasks": user_tasks,
        "injection_tasks": injection_tasks,
        "results": ours,
        "runs_executed": meter.runs,
        "llm_calls": meter.calls,
        "prompt_tokens": meter.prompt,
        "cached_tokens": meter.cached,
        "completion_tokens": meter.completion,
        "usd": round(meter.usd, 6),
        "minutes": round(minutes, 2),
    }
    if meter.runs:
        per_run = meter.usd / meter.runs
        full_banking = runs_needed(suite)
        all_suites = sum(runs_needed(s) for s in get_suites(BENCHMARK_VERSION).values())
        print(
            f"Cost per run: ${per_run:.5f} -> full banking ({full_banking} runs) ~${per_run * full_banking:.2f}, "
            f"all 4 suites ({all_suites} runs) ~${per_run * all_suites:.2f} per configuration"
        )
        summary.update(
            usd_per_run=round(per_run, 6),
            estimate_full_banking_usd=round(per_run * full_banking, 2),
            estimate_all_suites_usd=round(per_run * all_suites, 2),
        )
    else:
        print("No new runs executed (all results were already in the logdir), so no cost estimate.")

    out = Path("results/phase0_smoke.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved {out}; full conversations under {logdir}/{MODEL}/{SUITE}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
