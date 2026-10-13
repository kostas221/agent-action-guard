"""What every published table rests on, and a check that it still does (evidence.py, docs/evidence.md).

Three identities, kept apart:
- execution: the code a run executed, recorded in each repeat's manifest.json (manifest.py; "legacy" before 0.3);
- input: the traces themselves, one SHA-256 digest per configuration, repeat and suite (trace_digest);
- scoring: the code that turns traces into tables (SCORING_FILES), which manifests leave out on purpose.

PLANNED is the expected matrix: every published experiment's runs folder, configurations, attack and the repeats
of each suite. A planned repeat that is missing altogether is found here, which the per-repeat completeness check
(metrics.cohort_problems) cannot see. An experiment marked "planned" has not run yet: it is reported as pending.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from action_guard.manifest import NAME as MANIFEST
from action_guard.settings import ATTACK

SCORING_FILES = (
    "action_guard/attacks.py",
    "action_guard/guard_metrics.py",
    "action_guard/labels.py",
    "action_guard/metrics.py",
    "barriers.py",
    "bootstrap.py",
    "bounds.py",
    "compare.py",
    "report.py",
)
INDEX = Path("results") / "evidence.json"
TRACES = "*/*/*.json"  # under a repeat's <model>/<suite>: <task>/<attack or none>/<injection or none>.json


@dataclass(frozen=True)
class Planned:
    runs_dir: str
    configs: tuple[str, ...]
    repeats: tuple[tuple[str, int], ...]  # (suite, number of repeats)
    attack: str = ATTACK
    status: str = "published"  # or "planned": not run yet
    what: str = ""

    def cells(self):
        for config in self.configs:
            for suite, count in self.repeats:
                for rep in range(1, count + 1):
                    yield config, f"rep{rep}", suite


AUTOMATIC = (
    "guard-auto-follow-warnings",
    "guard-auto-hybrid-follow-warnings",
    "guard-auto-hybrid-mini-follow-warnings",
)
DEFENSES = ("defense-tool_filter", "defense-repeat_user_prompt", "defense-spotlighting_with_delimiting")
PLANNED = (
    Planned("runs", ("baseline",), (("banking", 3), ("slack", 3), ("travel", 1), ("workspace", 1)),
            what="the undefended agent (0.1; Slack repeats 2-3 added in 0.3)"),
    Planned("runs", ("guard-follow-warnings", "guard-oracle", "guard-reject-all"), (("banking", 3),),
            what="0.1: the hand-written policy with simulated users"),
    Planned("runs", ("guard-approve-all",), (("banking", 1),), what="0.1: approve-all control"),
    Planned("runs-v0.2", ("guard-follow-warnings", "guard-hybrid-follow-warnings"), (("banking", 3),),
            what="0.2: rules and hybrid"),
    Planned("runs-v0.2", ("guard-judge-follow-warnings",), (("banking", 1),), what="0.2: the judge alone"),
    Planned("runs-v0.2.0", ("guard-follow-warnings", "guard-hybrid-follow-warnings"), (("banking", 3),),
            what="0.2.0: final rules and hybrid"),
    Planned("runs-v0.3", AUTOMATIC[:2], (("slack", 3),), what="0.3: automatic policy on Slack"),
    Planned("runs-v0.3-banking", AUTOMATIC + DEFENSES, (("banking", 3),),
            what="0.3: automatic policy on banking and AgentDojo's defenses"),
    Planned("runs-v0.3-tool-knowledge", ("baseline", *AUTOMATIC), (("banking", 1),), attack="tool_knowledge",
            what="0.3: the second attack template"),
    Planned("runs-v0.4", AUTOMATIC, (("banking", 3),), what="0.4: automatic policy on banking"),
)  # fmt: skip


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha256(path: Path) -> str:
    """Of a file kept in git: line endings are those of the checkout (CRLF on Windows, LF elsewhere), so they are
    made LF first; the same file then has the same digest on every platform."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def scoring_identity(root: Path = Path(".")) -> dict:
    """The reporting code and the AgentDojo version that scores the traces; any change gives another identity."""
    from importlib.metadata import version

    files = {name: text_sha256(root / name) for name in SCORING_FILES}
    blob = json.dumps({"files": files, "agentdojo": version("agentdojo")}, sort_keys=True)
    return {
        "identity": hashlib.sha256(blob.encode()).hexdigest()[:12],
        "agentdojo": version("agentdojo"),
        "files": files,
    }


def trace_files(runs_dir: Path, config: str, rep: str, suite: str) -> list[Path]:
    return sorted((runs_dir / config / rep).glob(f"*/{suite}/{TRACES}"))


def trace_digest(runs_dir: Path, config: str, rep: str, suite: str) -> tuple[str, int]:
    """One digest over every trace of a suite in a repeat: their paths and their bytes. (digest, number of traces)"""
    digest = hashlib.sha256()
    files = trace_files(runs_dir, config, rep, suite)
    for path in files:
        digest.update(path.relative_to(runs_dir).as_posix().encode() + b"\0" + sha256(path).encode() + b"\n")
    return digest.hexdigest(), len(files)


def execution_identity(runs_dir: Path, config: str, rep: str) -> str:
    manifest = runs_dir / config / rep / MANIFEST
    if not manifest.exists():
        return "legacy"
    return json.loads(manifest.read_text(encoding="utf-8"))["fingerprint"][:12]


def write_result(out: Path, text: str, index: Path = INDEX) -> bool:
    """Write a result file, but never change one the evidence index lists as published: a correction or a new scorer
    goes to a new results version, so a published table stays the one it was. The same content may be written again
    (that is how reproduction is checked)."""
    if out.exists() and index.exists():
        published = json.loads(index.read_text(encoding="utf-8"))["results"]
        if out.as_posix() in published and out.read_text(encoding="utf-8") != text:
            print(f"\nNot saved: {out} is published (results/evidence.json) and this would change it; write a")
            print("correction to a new results version instead, and leave the published one as it is.")
            return False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return True


def result_hashes(root: Path = Path(".")) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): text_sha256(path) for path in sorted((root / "results").rglob("*.json"))}
