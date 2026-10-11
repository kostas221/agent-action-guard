"""One writer per suite and repeat: two processes on the same suite and repeat race on the trace files and crash
(CLAUDE.md, "Rules for running benchmarks"). Until 0.4 this rule was kept by hand; now the second process is refused.

The lock is the operating system's own (a byte-range lock on Windows, flock elsewhere), held while the suite runs
and released by the operating system if the process dies, so a crashed run never leaves a stale lock behind.
"""

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def run_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - held open for as long as the lock
    try:
        try:
            lock(handle)
        except OSError:
            raise AlreadyRunning(
                f"another process is already running this suite and repeat ({path}): wait for it to finish"
            ) from None
        try:
            yield
        finally:
            unlock(handle)
    finally:
        handle.close()


if sys.platform == "win32":
    import msvcrt

    def lock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def lock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
