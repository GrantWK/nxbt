"""Test Ctrl+C / SIGTERM shutdown: process cleanup and adapter restore.

Automatic (launches scripts/hw-measurements/_session.py, signals it, inspects the aftermath):
  sudo .venv/bin/python scripts/hw-measurements/shutdown.py                    # Ctrl+C while waiting for a Switch
  sudo .venv/bin/python scripts/hw-measurements/shutdown.py --signal TERM      # SIGTERM to the main process only
  sudo .venv/bin/python scripts/hw-measurements/shutdown.py --reconnect MAC --wait-connected   # while connected

Manual: run scripts/hw-measurements/_session.py yourself, press Ctrl+C, then:
  sudo .venv/bin/python scripts/hw-measurements/shutdown.py --check
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time

import psutil

import _common
from _common import RESULTS, HERE, adapter_powered, env_info, write_result
from _session import DEFAULT_SNAPSHOT

OVERRIDE = "/run/systemd/system/bluetooth.service.d/nxbt.conf"


def inspect(snapshot, wait_restore=5):
    """Reports leftover processes and adapter/bluetoothd state."""
    leftovers = []
    for pid in [snapshot["pid"], *snapshot["children"]]:
        try:
            p = psutil.Process(pid)
            if p.status() != psutil.STATUS_ZOMBIE:
                leftovers.append({"pid": pid, "status": p.status(), "cmd": " ".join(p.cmdline())[:120]})
        except psutil.NoSuchProcess:
            pass
    deadline = time.time() + wait_restore
    powered = adapter_powered()
    while powered != snapshot["powered_before"] and time.time() < deadline:
        time.sleep(0.25)
        powered = adapter_powered()
    return {
        "leftover_processes": leftovers,
        "powered_before": snapshot["powered_before"],
        "powered_after": powered,
        "adapter_restored": powered == snapshot["powered_before"],
        "bluetoothd_override_present": os.path.exists(OVERRIDE),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--signal", choices=["INT", "TERM"], default="INT")
    parser.add_argument("--reconnect", metavar="MAC")
    parser.add_argument("--wait-connected", action="store_true")
    parser.add_argument("--check", action="store_true", help="only inspect after a manual Ctrl+C")
    args = parser.parse_args()
    _common.require_bt_permission()

    if args.check:
        snapshot = json.loads(DEFAULT_SNAPSHOT.read_text())
        result = {"mode": "manual", "state_at_signal": snapshot["state"], **inspect(snapshot)}
    else:
        snap_path = RESULTS / "shutdown-snapshot.json"
        snap_path.unlink(missing_ok=True)
        cmd = [sys.executable, str(HERE / "_session.py"), "--snapshot", str(snap_path)]
        if args.reconnect:
            cmd += ["--reconnect", args.reconnect]
        if args.wait_connected:
            cmd += ["--wait-connected"]
        log_path = RESULTS / "shutdown-session.log"
        RESULTS.mkdir(exist_ok=True)
        with open(log_path, "w") as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.time() + 200
        while not snap_path.exists():
            if proc.poll() is not None or time.time() > deadline:
                sys.exit(f"session did not start; see {log_path}")
            time.sleep(0.2)
        time.sleep(1)
        snapshot = json.loads(snap_path.read_text())
        print(f"session ready ({snapshot['state']}), sending SIG{args.signal}...")

        sig = getattr(signal, f"SIG{args.signal}")
        t0 = time.perf_counter()
        if args.signal == "INT":
            os.killpg(proc.pid, sig)  # what a terminal Ctrl+C does
        else:
            os.kill(proc.pid, sig)
        try:
            proc.wait(timeout=20)
            exit_seconds = round(time.perf_counter() - t0, 2)
        except subprocess.TimeoutExpired:
            exit_seconds = None
        time.sleep(2)  # give orphaned children a moment
        result = {
            "mode": f"SIG{args.signal}",
            "state_at_signal": snapshot["state"],
            "main_exit_seconds": exit_seconds,
            "main_exit_code": proc.returncode,
            **inspect(snapshot),
            "session_log_tail": log_path.read_text()[-2000:],
        }
        if result["leftover_processes"] or exit_seconds is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # clean up for the next run
            except ProcessLookupError:
                pass
        _common.chown_tree(RESULTS)

    result["env"] = env_info()
    for key in ("mode", "state_at_signal", "main_exit_seconds", "main_exit_code",
                "leftover_processes", "powered_before", "powered_after", "bluetoothd_override_present"):
        if key in result:
            print(f"  {key}: {result[key]}")
    print(f"saved {write_result('shutdown', result)}")


if __name__ == "__main__":
    main()
