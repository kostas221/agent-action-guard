"""The trace archive of a release: every planned runs folder (action_guard/evidence.py, PLANNED) that is here and the
set-aside trials the docs mention, in one zip with a fixed order and fixed timestamps, so the same traces always give
the same bytes. Its SHA-256 goes into the release notes. No model calls.

    uv run python archive_traces.py --dry-run          # what would go in, and how many files; nothing written
    uv run python archive_traces.py --version 0.4.0    # dist/agent-action-guard-traces-v0.4.0.zip

Refused while evidence.py finds a problem with a published repeat: an archive holds only checked evidence.
Extracted in the repository root, it restores the folders as they were (README, "Reproduce the tables").
"""

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

from action_guard.evidence import PLANNED
from evidence import survey

# Runs set aside and described in the docs (kept so that what was discarded can be checked too)
TRIALS = (
    "runs/trial-before-trust-fix",
    "runs/trial-ratelimited",
    "runs/trial-v0.3-rep1-before-fix",
    "runs/trial-v0.3-hybrid-before-named-fix",
    "runs/gpt-4o-mini-2024-07-18",  # phase 0's smoke runs behind results/phase0_smoke.json
)
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def members(root: Path) -> list[Path]:
    folders = sorted({f"{p.runs_dir}/{config}" for p in PLANNED for config in p.configs} | set(TRIALS))
    files = []
    for folder in folders:
        if (root / folder).exists():
            files += [path for path in (root / folder).rglob("*") if path.is_file() and path.suffix != ".lock"]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", help="the release, e.g. 0.4.0")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(".")
    _, problems, _ = survey(root)
    files = members(root)
    tops = sorted({"/".join(path.relative_to(root).parts[:2]) for path in files})
    size = sum(path.stat().st_size for path in files)
    print(f"{len(files)} files, {size / 1e6:.1f} MB, from {len(tops)} folders:")
    for top in tops:
        print(f"  {top}")
    if problems:
        print(
            "\nNot archived: evidence.py finds problems with published repeats:" + "".join(f"\n  {p}" for p in problems)
        )
        return 1
    if args.dry_run:
        return 0
    if not args.version:
        ap.error("--version is needed to name the archive")
    out = Path("dist") / f"agent-action-guard-traces-v{args.version}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w") as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"\nSaved {out} ({out.stat().st_size / 1e6:.1f} MB)\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
