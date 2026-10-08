"""Keeps the computer from suspending while nxbt has a controller.

Suspend freezes nxbt and drops the Bluetooth link, ending any running macro.
nxbt takes a logind "sleep" inhibitor (via ``systemd-inhibit``), which blocks
suspend only: the screen can still blank and lock while macros keep running.
"""

import logging
import os
import shutil
import signal
import subprocess


class SleepInhibitor:
    def __init__(self, why="Controlling a Nintendo Switch"):
        self.why = why
        self._process = None

    @property
    def active(self):
        return self._process is not None and self._process.poll() is None

    def acquire(self):
        """Blocks suspend until release() or until nxbt exits. Does nothing
        where systemd-inhibit isn't available (e.g. Docker, Windows)."""
        if self.active:
            return
        if not (shutil.which("systemd-inhibit") and shutil.which("tail")):
            logging.getLogger("nxbt").debug("systemd-inhibit not found; not blocking suspend")
            return
        # `tail --pid` ends with this process, so the inhibitor can't outlive
        # nxbt even if it crashes or is killed.
        self._process = subprocess.Popen(
            [
                "systemd-inhibit", "--what=sleep", "--mode=block",
                "--who=nxbt", f"--why={self.why}",
                "tail", f"--pid={os.getpid()}", "-f", "/dev/null",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # so release() can stop tail too
        )

    def release(self):
        if self._process is None:
            return
        try:
            os.killpg(self._process.pid, signal.SIGTERM)
            self._process.wait(timeout=2)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(self._process.pid, signal.SIGKILL)
        self._process = None
