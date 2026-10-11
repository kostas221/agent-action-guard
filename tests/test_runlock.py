"""One writer per suite and repeat (action_guard/runlock.py)."""

import subprocess
import sys

import pytest

from action_guard.runlock import AlreadyRunning, run_lock


def test_a_second_writer_on_the_same_suite_and_repeat_is_refused(tmp_path):
    path = tmp_path / "rep1" / ".banking.lock"
    with run_lock(path):
        with pytest.raises(AlreadyRunning, match="already running"):
            with run_lock(path):
                pass
        with run_lock(tmp_path / "rep1" / ".slack.lock"):  # another suite runs alongside
            pass
    with run_lock(path):  # free again once the first one ends
        pass


def test_another_process_is_refused_while_the_lock_is_held(tmp_path):
    path = tmp_path / ".banking.lock"
    code = (
        "import sys; from pathlib import Path; from action_guard.runlock import AlreadyRunning, run_lock\n"
        "try:\n    with run_lock(Path(sys.argv[1])): sys.exit(0)\nexcept AlreadyRunning: sys.exit(3)"
    )
    with run_lock(path):
        held = subprocess.run([sys.executable, "-c", code, str(path)], check=False)
    free = subprocess.run([sys.executable, "-c", code, str(path)], check=False)
    assert (held.returncode, free.returncode) == (3, 0)
