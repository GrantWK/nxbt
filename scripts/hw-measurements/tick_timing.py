"""Measure the controller loop's real tick timing, macro step accuracy and
macro start latency while connected to a Switch.

Put the Switch on the HOME menu (not in a game): the macro only moves the
cursor right and left.

Usage:
  sudo .venv/bin/python scripts/hw-measurements/tick_timing.py --reconnect MAC   # recommended
  sudo .venv/bin/python scripts/hw-measurements/tick_timing.py                   # pair first
"""

import argparse
import time

import _common
import _instrument
from _common import (
    RESULTS, SAFE_MACRO, adapter_info, env_info, interval_stats, poll_state,
    start_controller, write_result,
)

from nxbt import Nxbt


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reconnect", metavar="MAC")
    parser.add_argument("--seconds", type=float, default=15, help="length of idle and macro phases")
    args = parser.parse_args()
    _common.require_bt_permission()
    _common.require_fork()

    raw_dir = RESULTS / f"tick_timing-raw-{time.strftime('%Y%m%d-%H%M%S')}"
    _instrument.install(raw_dir)
    result = {"env": env_info(), "adapter": adapter_info()}

    nx = Nxbt(disable_logging=True)
    idx = start_controller(nx, args.reconnect)
    if poll_state(nx, idx, until="connected", timeout=180) != "connected":
        print(nx.state[idx].get("errors"))
        return
    time.sleep(2)

    print(f"idle phase ({args.seconds}s)...")
    idle_start = time.perf_counter()
    time.sleep(args.seconds)

    print(f"macro phase ({args.seconds}s, cursor moves right/left)...")
    repeats = max(1, int(args.seconds / 0.3))
    macro_submit = time.perf_counter()
    nx.macro(idx, SAFE_MACRO * repeats, block=False)
    time.sleep(args.seconds + 1)
    macro_end = time.perf_counter()

    nx.remove_controller(idx)
    data = next(d for d in _instrument.load(raw_dir).values() if d["ticks"])
    ticks, events = data["ticks"], data["events"]
    starts = [e[1] for e in events if e[0] == "macro_start" and e[1] >= macro_submit]
    errors = _instrument.macro_step_errors([e for e in events if e[1] >= macro_submit])

    def window(ts, a, b):
        return [t for t in ts if a <= t < b]

    result.update({
        "idle_ticks": interval_stats(window(ticks, idle_start, macro_submit)),
        "macro_ticks": interval_stats(window(ticks, macro_submit, macro_end)),
        "macro_start_latency_ms": round((starts[0] - macro_submit) * 1000, 2) if starts else None,
        "macro_step_error_ms": {
            "count": len(errors),
            "mean": round(sum(errors) / len(errors), 3) if errors else None,
            "max": max(errors) if errors else None,
        },
        "send_intervals": interval_stats(window(data["sends"], idle_start, macro_end), target_ms=0),
        "switch_msg_intervals": interval_stats(window(data["recvs"], idle_start, macro_end), target_ms=0),
        "flushes_ms": [round(e[2] * 1000, 2) for e in events if e[0] == "flush" and len(e) > 2],
        "adapter_after": adapter_info(),
    })
    for key in ("idle_ticks", "macro_ticks"):
        s = result[key]
        print(f"{key}: {s.get('effective_hz')} Hz, p50 {s.get('p50_ms')} ms, "
              f"p99 {s.get('p99_ms')} ms, max {s.get('max_ms')} ms, lag1 {s.get('lag1_autocorr')}")
    print(f"macro start latency: {result['macro_start_latency_ms']} ms; "
          f"step error mean {result['macro_step_error_ms']['mean']} ms")
    _common.chown_tree(raw_dir)
    print(f"saved {write_result('tick_timing', result)}  (raw: {raw_dir})")


if __name__ == "__main__":
    main()
