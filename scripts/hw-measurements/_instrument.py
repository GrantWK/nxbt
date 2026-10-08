"""Records per-tick timing inside nxbt's controller process without editing nxbt.

install() wraps nxbt functions in the parent; forked controller processes
inherit the wrappers. Each controller process appends JSON lines to
<out_dir>/instrument-<pid>.jsonl. Timestamps are time.perf_counter(), which
is CLOCK_MONOTONIC on Linux and therefore comparable across processes.
"""

import json
import os
import time
from pathlib import Path

FLUSH_EVERY_TICKS = 4000


class _Recorder:
    def __init__(self, out_dir):
        self.path = Path(out_dir) / f"instrument-{os.getpid()}.jsonl"
        self.ticks, self.sends, self.recvs, self.events = [], [], [], []

    def flush(self):
        start = time.perf_counter()
        chunk = {
            "pid": os.getpid(),
            "ticks": self.ticks,
            "sends": self.sends,
            "recvs": self.recvs,
            "events": self.events,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(chunk) + "\n")
        self.ticks, self.sends, self.recvs = [], [], []
        self.events = [["flush", start, time.perf_counter() - start]]


_recorder = None
_out_dir = None


def _rec():
    global _recorder
    if _recorder is None or _recorder.path.name != f"instrument-{os.getpid()}.jsonl":
        _recorder = _Recorder(_out_dir)
    return _recorder


class _SocketProxy:
    """Wraps the Switch interrupt socket to timestamp traffic."""

    def __init__(self, sock):
        self._sock = sock

    def sendall(self, data):
        _rec().sends.append(time.perf_counter())
        return self._sock.sendall(data)

    def recv(self, n):
        data = self._sock.recv(n)
        if data:
            _rec().recvs.append(time.perf_counter())
        return data

    def __getattr__(self, name):
        return getattr(self._sock, name)


def install(out_dir):
    global _out_dir
    _out_dir = str(out_dir)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    from nxbt.controller import server as server_mod
    from nxbt.controller.input import InputParser

    orig_spi = InputParser.set_protocol_input
    orig_mainloop = server_mod.ControllerServer.mainloop
    orig_run = server_mod.ControllerServer.run

    def set_protocol_input(self, state=None):
        rec = _rec()
        t = time.perf_counter()
        before_id = self.current_macro_id
        before_cmds = self.current_macro_commands
        orig_spi(self, state)
        rec.ticks.append(t)
        if self.current_macro_id != before_id and self.current_macro_id:
            rec.events.append(["macro_start", t, self.current_macro_id])
        cmds = self.current_macro_commands
        if cmds is not None and cmds is not before_cmds:
            rec.events.append(
                ["step", self.macro_timer_start, " ".join(cmds), self.macro_timer_length]
            )
        if before_cmds is not None and cmds is None:
            rec.events.append(["step_end", time.perf_counter()])
        if len(rec.ticks) >= FLUSH_EVERY_TICKS:
            rec.flush()

    def mainloop(self, itr, ctrl):
        _rec().events.append(["mainloop_start", time.perf_counter()])
        try:
            return orig_mainloop(self, _SocketProxy(itr), ctrl)
        finally:
            _rec().events.append(["mainloop_end", time.perf_counter()])
            _rec().flush()

    def run(self, reconnect_address=None):
        _rec().events.append(["controller_run", time.perf_counter()])
        _rec().flush()
        try:
            return orig_run(self, reconnect_address)
        finally:
            _rec().flush()

    InputParser.set_protocol_input = set_protocol_input
    server_mod.ControllerServer.mainloop = mainloop
    server_mod.ControllerServer.run = run


def load(out_dir):
    """Merges every instrument-*.jsonl in out_dir into {pid: {...}}."""
    merged = {}
    for path in sorted(Path(out_dir).glob("instrument-*.jsonl")):
        for line in path.read_text().splitlines():
            chunk = json.loads(line)
            m = merged.setdefault(
                chunk["pid"], {"ticks": [], "sends": [], "recvs": [], "events": []}
            )
            for key in ("ticks", "sends", "recvs", "events"):
                m[key].extend(chunk[key])
    return merged


def macro_step_errors(events):
    """For each macro step: actual duration (to the next step start or
    step end) minus the requested duration, in ms."""
    marks = [e for e in events if e[0] in ("step", "step_end")]
    errors = []
    for cur, nxt in zip(marks, marks[1:]):
        if cur[0] != "step":
            continue
        actual = (nxt[1] - cur[1]) * 1000
        errors.append(round(actual - cur[3] * 1000, 3))
    return errors
