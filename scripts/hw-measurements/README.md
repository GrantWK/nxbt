# Hardware measurements

Measurement scripts only; nothing here changes nxbt. Results are written to
`scripts/hw-measurements/results/` (git-ignored) as JSON.

## Setup

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e .
```

All hardware scripts use the Bumble backend on `hci-socket:0` and need
`CAP_NET_ADMIN`, so run them with `sudo .venv/bin/python ...`.

**While a hardware script runs, Bumble takes exclusive control of the
adapter**: other Bluetooth devices on this PC disconnect until it exits.
nxbt does not touch `bluetoothd` itself with the Bumble backend.

## Scripts

| Script | Hardware | Measures |
|---|---|---|
| `loop_bench.py` | none | Tick timing on a local socket; CPU of `run_with_check` and `wait_for_connection` |
| `pair_reconnect.py` | adapter + Switch | Pairing time; behavior when the Switch sleeps/wakes; `--reconnect MAC` path |
| `tick_timing.py` | adapter + Switch | Real tick timing, macro step error, macro start latency, send/receive rates |
| `cpu_usage.py` | adapter + Switch | CPU per process: idle, waiting for Switch, connected, macro |
| `shutdown.py` | adapter (Switch optional) | Ctrl+C / SIGTERM: exit time, leftover processes, adapter power restored |

Suggested order:

1. `pair_reconnect.py` with the Switch on **Controllers > Change Grip/Order**.
   Note the printed Switch address, then sleep and wake the Switch.
2. `pair_reconnect.py --reconnect MAC` with the Switch on the HOME menu.
3. `tick_timing.py --reconnect MAC` with the Switch on the HOME menu
   (the macro only moves the cursor right/left).
4. `cpu_usage.py`: keep the Switch off the grip menu until prompted.
5. `shutdown.py`, then `shutdown.py --signal TERM`, then
   `shutdown.py --reconnect MAC --wait-connected`.

`_common.py` and `_instrument.py` are shared helpers. `_instrument.py`
wraps nxbt functions in the parent process so forked controller processes
record per-tick timestamps; it forces the `fork` start method.
