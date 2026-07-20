from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock

from openpyxl.workbook.workbook import Workbook

_LOCKS_GUARD = Lock()
_PATH_LOCKS: dict[str, RLock] = {}


@contextmanager
def path_lock(path: Path) -> Iterator[None]:
    """Serialize access to one filesystem path inside a JobAuto process."""
    key = os.path.normcase(str(path.expanduser().resolve()))
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.setdefault(key, RLock())
    with lock:
        yield


@contextmanager
def tracker_lock(path: Path) -> Iterator[None]:
    with path_lock(path):
        yield


def save_workbook_atomically(workbook: Workbook, path: Path) -> None:
    """Replace a tracker only after a complete XLSX archive has been written."""
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.stem}.",
        suffix=target.suffix or ".xlsx",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        workbook.save(temporary)
        for attempt in range(5):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.02 * (2**attempt))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
