"""Run AgentDojo with one configuration and save every run under runs/<config>/rep<k>/.

    uv run python run_benchmark.py --suites banking           # one suite
    uv run python run_benchmark.py                            # all four suites
    uv run python run_benchmark.py --suites banking --rep 2   # a repeat, to measure run-to-run noise
    uv run python run_benchmark.py --config guard-follow-warnings --suites banking   # with the approval guard
    uv run python run_benchmark.py --config guard-oracle --suites banking --user-tasks user_task_2   # a quick trial

Interrupted runs resume where they stopped: finished runs are skipped, never paid twice.
Suites can run in parallel from separate terminals, since each one writes its own files.
"""

import argparse
import sys
import time
import warnings
from pathlib import Path

from agentdojo.attacks.attack_registry import load_attack
from agentdojo.benchmark import benchmark_suite_with_injections, benchmark_suite_without_injections
from agentdojo.logging import Logger, OutputLogger
from agentdojo.task_suite.load_suites import get_suite
from dotenv import load_dotenv

from action_guard.manifest import ExperimentMismatch, check_or_write
from action_guard.metrics import expected_runs
from action_guard.pipelines import CONFIGS, build_pipeline, guarded_suites
from action_guard.settings import ATTACK, BENCHMARK_VERSION, DEFAULT_MODEL, SUITES
from action_guard.usage import BudgetExceeded, UsageMeter

warnings.filterwarnings("ignore", category=DeprecationWarning)  # pydantic v1-style calls inside AgentDojo


def show_progress(pipeline, meter: UsageMeter) -> None:
    """Print one line per executed run: which task, which injection, money spent so far."""
    query = pipeline.query
    executed = 0

    def query_with_progress(*args, **kwargs):
        nonlocal executed
        result = query(*args, **kwargs)
        executed += 1
        context = getattr(Logger.get(), "context", {})
        injection = context.get("injection_task_id") or "no attack"
        print(f"  {executed:4d}  {context.get('user_task_id')} x {injection}   ${meter.usd:.4f}", flush=True)
        return result

    pipeline.query = query_with_progress


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="baseline", choices=CONFIGS)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--suites", nargs="+", default=list(SUITES), choices=SUITES)
    ap.add_argument("--rep", type=int, default=1, help="repeat number; repeats measure run-to-run noise")
    ap.add_argument("--user-tasks", nargs="+", help="only these user tasks (a quick trial); the rest can run later")
    ap.add_argument("--max-usd", type=float, default=3.0, help="hard stop on spend for this invocation")
    ap.add_argument("--runs-dir", default="runs")
    args = ap.parse_args()
    allowed = guarded_suites(args.config)
    if args.config.startswith("guard-") and set(args.suites) - set(allowed):
        ap.error(f"{args.config} has an approval policy for {', '.join(allowed)} only: choose --suites among them")

    load_dotenv(".env")
    meter = UsageMeter(args.max_usd)
    logdir = Path(args.runs_dir) / args.config / f"rep{args.rep}"
    try:  # one repeat is one experiment: resumed only with the same setup and the same run files
        experiment = check_or_write(logdir, args.config, args.model)
    except ExperimentMismatch as exc:
        print(f"!! {exc}")
        return 2
    print(f"{args.config} | {args.model} | AgentDojo {BENCHMARK_VERSION} | attack {ATTACK} | saving to {logdir}/")
    print(f"experiment {experiment['fingerprint'][:12]} (manifest.json in that folder)")

    started = time.time()
    try:
        for name in args.suites:
            suite = get_suite(BENCHMARK_VERSION, name)
            pipeline = build_pipeline(args.config, args.model, meter, name)  # each suite has its own policy
            show_progress(pipeline, meter)
            print(f"\n== {name}: {expected_runs(suite)} runs in total (already finished ones are skipped)", flush=True)
            with OutputLogger(str(logdir)):
                benchmark_suite_without_injections(
                    pipeline,
                    suite,
                    logdir=logdir,
                    force_rerun=False,
                    user_tasks=args.user_tasks,
                    benchmark_version=BENCHMARK_VERSION,
                )
                attack = load_attack(ATTACK, suite, pipeline)
                benchmark_suite_with_injections(
                    pipeline,
                    suite,
                    attack,
                    logdir=logdir,
                    force_rerun=False,
                    user_tasks=args.user_tasks,
                    verbose=False,
                    benchmark_version=BENCHMARK_VERSION,
                )
    except BudgetExceeded as e:
        print(f"\n!! {e}. Finished runs are saved; run the same command again to continue.")
        return 1

    minutes = (time.time() - started) / 60
    print(f"\nDone in {minutes:.1f} min: ${meter.usd:.4f} spent in this invocation ({meter.calls} LLM calls).")
    print(f"Results: uv run python report.py --config {args.config}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
