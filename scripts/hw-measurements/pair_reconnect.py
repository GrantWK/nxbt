"""Pair (or reconnect) over Bumble's HCI socket, then watch the connection
while you put the Switch to sleep and wake it.

Usage:
  sudo .venv/bin/python scripts/hw-measurements/pair_reconnect.py                 # pair
  sudo .venv/bin/python scripts/hw-measurements/pair_reconnect.py --reconnect MAC # reconnect

After "connected": press the Switch's power button to sleep it, wait ~10 s,
wake it, and watch the state log. Press Ctrl+C when done.
"""

import argparse
import time

import _common
from _common import adapter_info, env_info, poll_state, start_controller, write_result

from nxbt import Nxbt


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reconnect", metavar="MAC")
    parser.add_argument("--watch", type=float, default=300, help="seconds to watch after connecting")
    args = parser.parse_args()
    _common.require_bt_permission()

    result = {"env": env_info(), "adapter_before": adapter_info(), "mode": "reconnect" if args.reconnect else "pair"}
    log = []
    nx = Nxbt(disable_logging=True)
    t0 = time.perf_counter()
    idx = None
    try:
        idx = start_controller(nx, args.reconnect)
        final = poll_state(nx, idx, until="connected", timeout=180, log=log)
        result["connect_seconds"] = round(time.perf_counter() - t0, 2)
        result["connect_result"] = final
        if final == "connected":
            result["switch_address"] = nx.state[idx]["last_connection"]
            print(f"Connected to {result['switch_address']}. Now sleep the Switch, wait, wake it. Ctrl+C to finish.")
            poll_state(nx, idx, timeout=args.watch, log=log)
        elif final == "crashed":
            result["errors"] = nx.state[idx]["errors"]
            print(result["errors"])
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        result["state_log"] = log
        if idx is not None:
            try:
                nx.remove_controller(idx)
            except Exception as e:
                result["remove_error"] = repr(e)
        time.sleep(1)
        result["adapter_after"] = adapter_info()
        print(f"saved {write_result('pair_reconnect', result)}")


if __name__ == "__main__":
    main()
