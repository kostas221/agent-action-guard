"""What a repeat ran with, so that runs made with different code or settings are never resumed or pooled as one.

Each repeat folder (runs-dir/config/repN) gets a manifest.json when its first run starts: the configuration, the
models, the benchmark, the attack, and a fingerprint of the files that decide what a run does (the guard, the
planner, the judge, the pipelines, the checked tools files, the locked dependencies). The commit is recorded for
reference only: a change to the README or to the reporting code does not make two repeats different experiments.
Folders started before manifests existed stay "legacy": they are never given today's identity.
"""

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from action_guard.settings import ATTACK, BENCHMARK_VERSION, HYBRID_JUDGE_VOTES, JUDGE_MODEL

NAME = "manifest.json"
LEGACY = "legacy"
RUN_FILES = (  # the code a run executes; reporting code (metrics, report.py, compare.py) is left out on purpose
    "action_guard/approval.py",
    "action_guard/automatic.py",
    "action_guard/banking.py",
    "action_guard/guard.py",
    "action_guard/judge.py",
    "action_guard/pipelines.py",
    "action_guard/planner.py",
    "action_guard/settings.py",
    "action_guard/usage.py",
    "run_benchmark.py",
    "uv.lock",
)


class ExperimentMismatch(Exception):
    pass


def file_hashes(root: Path = Path(".")) -> dict[str, str]:
    """sha256 of every file that decides what a run does, line endings made uniform (Windows checkouts add CR)."""
    paths = [*RUN_FILES, *sorted(p.relative_to(root).as_posix() for p in (root / "policies").glob("*.json"))]
    return {
        path: hashlib.sha256((root / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in paths
        if (root / path).exists()
    }


def fingerprint(hashes: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{p}:{h}" for p, h in sorted(hashes.items())).encode()).hexdigest()


def identity(config: str, model: str, attack: str = ATTACK) -> dict:
    from action_guard.pipelines import AUTOMATIC, DEFENSES, JUDGED

    planner = AUTOMATIC[config][1] if config in AUTOMATIC else None
    judged = config in JUDGED or (config in AUTOMATIC and AUTOMATIC[config][0] == "hybrid")
    return {
        "config": config,
        "agent_model": model,
        "planner_model": planner,
        "judge_model": JUDGE_MODEL if judged else None,
        "judge_votes": (HYBRID_JUDGE_VOTES if "hybrid" in config else 1) if judged else None,
        "agentdojo_defense": DEFENSES.get(config),
        "benchmark_version": BENCHMARK_VERSION,
        "attack": attack,
        "agentdojo_version": version("agentdojo"),
    }


def git_commit(root: Path = Path(".")) -> tuple[str | None, bool]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None, False
    return commit.stdout.strip(), bool(status.stdout.strip())


def build(config: str, model: str, root: Path = Path("."), *, attack: str = ATTACK) -> dict:
    hashes = file_hashes(root)
    commit, dirty = git_commit(root)
    return {
        "schema": 1,
        "identity": identity(config, model, attack),
        "fingerprint": fingerprint(hashes),
        "files": hashes,
        "git_commit": commit,
        "git_dirty": dirty,
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def load(logdir: Path) -> dict | None:
    path = logdir / NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def has_runs(logdir: Path) -> bool:
    return any(logdir.glob("*/*/*/*/*.json"))  # <pipeline>/<suite>/<task>/<attack>/<injection>.json


def check_or_write(logdir: Path, config: str, model: str, root: Path = Path("."), *, attack: str = ATTACK) -> dict:
    """Start a repeat with a manifest, or resume one only with the same identity and the same run files."""
    current = build(config, model, root, attack=attack)
    saved = load(logdir)
    if saved is None:
        if has_runs(logdir):
            raise ExperimentMismatch(
                f"{logdir} holds runs made before run manifests: what they ran with is not recorded, so they cannot "
                "be resumed as one experiment. Move the folder aside, or use another --rep."
            )
        logdir.mkdir(parents=True, exist_ok=True)
        (logdir / NAME).write_text(json.dumps(current, indent=2), encoding="utf-8")
        return current
    problems = [
        f"{key}: {saved['identity'].get(key)!r} -> {value!r}"
        for key, value in current["identity"].items()
        if saved["identity"].get(key) != value
    ]
    changed = sorted(
        p for p in {*saved["files"], *current["files"]} if saved["files"].get(p) != current["files"].get(p)
    )
    if problems or changed:
        raise ExperimentMismatch(
            f"{logdir} was started with a different setup ({'; '.join(problems) or 'same settings'}; files changed: "
            f"{', '.join(changed) or 'none'}). Its runs would not be one experiment: move the folder aside "
            "(for example to runs/trial-<reason>/), or use another --rep."
        )
    return saved


def label(logdir: Path) -> str:
    """The repeat's experiment: its fingerprint, or "legacy" for a folder made before manifests."""
    saved = load(logdir)
    return saved["fingerprint"] if saved else LEGACY


def attack_of(logdir: Path) -> str | None:
    """The attack a repeat was run with, or None for a folder made before manifests (all under the main attack)."""
    saved = load(logdir)
    return saved.get("identity", {}).get("attack") if saved else None
