"""No-hardware benchmark of nxbt's controller loop and wait loops.

Runs the real ControllerServer.mainloop against a local socket pair (no
Bluetooth, no Switch) and measures:
  1. tick interval timing, idle and while a macro runs
  2. CPU used by ControllerServer.run_with_check while it waits
  3. CPU used by Nxbt.wait_for_connection while it waits

Usage: python scripts/hw-measurements/loop_bench.py [--seconds 5]
"""

import argparse
import copy
import json
import logging
import queue
import socket
import tempfile
import threading
import time

import _common  # noqa: F401  (puts the repo on sys.path)
import _instrument
from _common import interval_stats, write_result, env_info

from nxbt.controller.controller import ControllerTypes
from nxbt.controller.server import ControllerServer
from nxbt.nxbt import DIRECT_INPUT_PACKET, Nxbt


class FakeBackend:
    address = "00:11:22:33:44:55"

    def shutdown(self):
        pass


def bench_ticks(seconds):
    raw_dir = tempfile.mkdtemp(prefix="nxbt-bench-")
    _instrument.install(raw_dir)
    logging.getLogger("nxbt").setLevel(logging.INFO)  # matches non-debug runs

    itr, switch_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    itr.setblocking(False)
    received = []

    def drain():
        while True:
            try:
                if not switch_side.recv(512):
                    return
                received.append(time.perf_counter())
            except OSError:
                return

    threading.Thread(target=drain, daemon=True).start()

    state = {
        "state": "connected",
        "direct_input": copy.deepcopy(DIRECT_INPUT_PACKET),
        "finished_macros": [],
    }
    tasks = queue.Queue()
    server = ControllerServer(
        ControllerTypes.PRO_CONTROLLER, backend=FakeBackend(), state=state, task_queue=tasks
    )
    loop = threading.Thread(target=server.mainloop, args=(itr, None))
    loop.start()

    time.sleep(seconds)  # idle phase
    macro_submit = time.perf_counter()
    steps = int(seconds / 0.1)
    tasks.put({"type": "macro", "macro": "DPAD_RIGHT 0.05s\n0.05s\n" * steps, "macro_id": "bench"})
    time.sleep(seconds + 0.5)
    state["state"] = "removing"
    loop.join(5)
    switch_side.close()

    data = next(iter(_instrument.load(raw_dir).values()))
    ticks = data["ticks"]
    idle = [t for t in ticks if t < macro_submit]
    busy = [t for t in ticks if t >= macro_submit]
    errors = _instrument.macro_step_errors(data["events"])
    return {
        "idle_ticks": interval_stats(idle),
        "macro_ticks": interval_stats(busy),
        "macro_step_error_ms": {
            "count": len(errors),
            "mean": round(sum(errors) / len(errors), 3) if errors else None,
            "max": max(errors) if errors else None,
        },
        "packets_sent": len(data["sends"]),
        "send_hz": round(len(data["sends"]) / (ticks[-1] - ticks[0]), 1),
    }


def cpu_fraction(fn):
    """CPU seconds used by this process per wall second while fn runs."""
    wall0, cpu0 = time.perf_counter(), time.process_time()
    fn()
    return round((time.process_time() - cpu0) / (time.perf_counter() - wall0), 3)


def bench_run_with_check(seconds):
    from concurrent.futures import ThreadPoolExecutor

    server = ControllerServer.__new__(ControllerServer)
    server.state = {"state": "connecting"}
    server.backend = FakeBackend()

    def baseline():
        with ThreadPoolExecutor() as ex:
            ex.submit(time.sleep, seconds).result()

    return {
        "baseline_future_result": cpu_fraction(baseline),
        "run_with_check": cpu_fraction(lambda: server.run_with_check(time.sleep, seconds)),
    }


def bench_wait_for_connection(seconds):
    import psutil

    nx = Nxbt(disable_logging=True)
    manager = psutil.Process(nx.resource_manager._process.pid)
    nx.manager_state[0] = {"state": "connecting"}

    def release():
        time.sleep(seconds)
        nx.manager_state[0] = {"state": "connected"}

    m0 = sum(manager.cpu_times()[:2])
    threading.Thread(target=release, daemon=True).start()
    caller = cpu_fraction(lambda: nx.wait_for_connection(0))
    manager_cpu = round((sum(manager.cpu_times()[:2]) - m0) / seconds, 3)
    return {"caller_process": caller, "manager_process": manager_cpu}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=5)
    args = parser.parse_args()

    result = {"env": env_info()}
    print("1/3 tick timing...")
    result["ticks"] = bench_ticks(args.seconds)
    print("2/3 run_with_check CPU...")
    result["run_with_check_cpu"] = bench_run_with_check(min(args.seconds, 3))
    print("3/3 wait_for_connection CPU...")
    result["wait_for_connection_cpu"] = bench_wait_for_connection(min(args.seconds, 3))

    print(json.dumps({k: v for k, v in result.items() if k != "env"}, indent=2))
    print(f"saved {write_result('loop_bench', result)}")


if __name__ == "__main__":
    main()
