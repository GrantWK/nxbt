"""Minimal nxbt session for shutdown tests: starts a controller, writes a
snapshot of its process tree, then idles until interrupted. Deliberately
does not catch KeyboardInterrupt, like a typical script built on nxbt.

Run directly and press Ctrl+C, then check with:
  sudo .venv/bin/python scripts/hw-measurements/shutdown.py --check
"""

import argparse
import json
import os
import time

import psutil

import _common
from _common import RESULTS, adapter_powered, poll_state, start_controller

from nxbt import Nxbt

DEFAULT_SNAPSHOT = RESULTS / "session-snapshot.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT))
    parser.add_argument("--reconnect", metavar="MAC")
    parser.add_argument("--wait-connected", action="store_true")
    args = parser.parse_args()
    _common.require_bt_permission()

    powered_before = adapter_powered()
    nx = Nxbt(disable_logging=True)
    idx = start_controller(nx, args.reconnect)
    if args.wait_connected:
        poll_state(nx, idx, until="connected", timeout=180)
    state = nx.state[idx]["state"]
    tree = [p.pid for p in psutil.Process().children(recursive=True)]
    RESULTS.mkdir(exist_ok=True)
    with open(args.snapshot, "w") as f:
        json.dump({"pid": os.getpid(), "children": tree, "state": state,
                   "powered_before": powered_before, "t": time.time()}, f)
    print(f"ready ({state}); press Ctrl+C to stop")
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
