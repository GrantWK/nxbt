"""Measure CPU use of every nxbt process through setup, waiting for the
Switch, connected idle, and a macro.

Keep the Switch AWAY from Change Grip/Order until the script says so: the
first wait phase measures CPU while nobody connects.

Usage: sudo .venv/bin/python scripts/hw-measurements/cpu_usage.py [--seconds 10]
"""

import argparse
import time

import psutil

import _common
from _common import SAFE_MACRO, CpuSampler, env_info, start_controller, poll_state, write_result

from nxbt import Nxbt


def label_children(sampler, parent_pid, role, known):
    try:
        for child in psutil.Process(parent_pid).children():
            if child.pid not in known:
                sampler.roles[child.pid] = role
                known.add(child.pid)
    except psutil.Error:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    _common.require_bt_permission()
    n = args.seconds

    sampler = CpuSampler()
    sampler.start()
    nx = Nxbt(disable_logging=True)
    sampler.roles[nx.resource_manager._process.pid] = "manager (parent)"
    sampler.roles[nx.controllers.pid] = "command manager"
    time.sleep(1)
    known = set()
    label_children(sampler, nx.controllers.pid, "manager (command)", known)

    sampler.phase = "1 nxbt idle, no controller"
    time.sleep(n)

    sampler.phase = "2 setup + waiting for Switch"
    idx = start_controller(nx)
    print("  (do NOT connect yet)")
    label_children(sampler, nx.controllers.pid, "controller", known)
    time.sleep(n)

    sampler.phase = "3 Nxbt.wait_for_connection()"
    print("Now open Change Grip/Order on the Switch to connect...")
    nx.wait_for_connection(idx)

    sampler.phase = "4 connected idle"
    time.sleep(n)

    sampler.phase = "5 macro"
    nx.macro(idx, SAFE_MACRO * max(1, int(n / 0.3)), block=False)
    time.sleep(n)

    sampler.phase = "6 removing"
    nx.remove_controller(idx)
    poll_state(nx, idx, timeout=1)
    sampler.stop()

    summary = sampler.summary()
    for key, cpu in summary.items():
        print(f"  {cpu:6.1f}%  {key}")
    result = {"env": env_info(), "cpu_percent_by_phase_role": summary, "samples": sampler.samples}
    print(f"saved {write_result('cpu_usage', result)}")


if __name__ == "__main__":
    main()
