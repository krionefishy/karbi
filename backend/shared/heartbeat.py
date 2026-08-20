"""Liveness heartbeat for worker containers.

The compose healthchecks consider a worker healthy while the mtime of
``/tmp/heartbeat`` is younger than 15 minutes. Every worker main loop must call
:func:`touch_heartbeat` at least that often — on each iteration is fine, the
call is a couple of syscalls.
"""

import os
import tempfile
from contextlib import suppress
from pathlib import Path

DEFAULT_HEARTBEAT_PATH = Path("/tmp/heartbeat")


def touch_heartbeat(path: str | Path = DEFAULT_HEARTBEAT_PATH) -> None:
    """Atomically refresh the mtime of the heartbeat file.

    A fresh file is created next to the target and moved over it with
    ``os.replace``, so a healthcheck never observes a missing or half-written
    file, and a crash mid-call cannot leave the target in a broken state.
    """
    target = Path(path)
    fd, staging = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}-")
    try:
        os.close(fd)
        os.replace(staging, target)
    except BaseException:
        with suppress(OSError):
            os.unlink(staging)
        raise
