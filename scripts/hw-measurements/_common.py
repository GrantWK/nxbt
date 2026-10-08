"""Shared helpers for the hardware measurement scripts."""

import json
import multiprocessing
import os
import platform
import statistics
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RESULTS = HERE / "results"

# Make `import nxbt` work without installing the package.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

TICK_TARGET_MS = 1000 / 132


def require_fork():
    """The instrumentation is installed in the parent and must be inherited
    by nxbt's controller processes, which only happens with fork."""
    method = multiprocessing.get_start_method(allow_none=True)
    if method is None:
        multiprocessing.set_start_method("fork")
    elif method != "fork":
        print(f"warning: start method is {method!r}; forcing 'fork' for instrumentation")
        multiprocessing.set_start_method("fork", force=True)


def _chown_to_invoking_user(path):
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if uid and gid:
        os.chown(path, int(uid), int(gid))


def chown_tree(path):
    path = Path(path)
    for p in [path, *path.rglob("*")]:
        _chown_to_invoking_user(p)


def write_result(name, data):
    """Saves data as scripts/hw-measurements/results/<name>-<timestamp>.json, owned by the
    invoking user even under sudo. Returns the path."""
    RESULTS.mkdir(exist_ok=True)
    _chown_to_invoking_user(RESULTS)
    path = RESULTS / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    _chown_to_invoking_user(path)
    return path


def git_commit():
    try:
        head = (REPO / ".git" / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            return (REPO / ".git" / head[5:]).read_text().strip()
        return head
    except OSError:
        return None


def adapter_info(index=0):
    info = {"index": index}
    dev = Path(f"/sys/class/bluetooth/hci{index}/device")
    try:
        path = dev.resolve()
        while path != path.parent and not (path / "idVendor").exists():
            path = path.parent
        info["usb_id"] = f"{(path / 'idVendor').read_text().strip()}:{(path / 'idProduct').read_text().strip()}"
        info["driver"] = (dev / "driver").resolve().name
    except OSError:
        pass
    info["powered"] = adapter_powered(index)
    return info


def adapter_powered(index=0):
    """Reads the adapter's power state from the kernel (read-only)."""
    try:
        from nxbt.backends.internal.mgmt import MgmtClient

        with MgmtClient() as mgmt:
            return "POWERED" in mgmt.read_controller_info(index).get_current_settings()
    except Exception as e:
        return f"unknown: {e}"


def env_info():
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "start_method": multiprocessing.get_start_method(allow_none=True),
        "euid": os.geteuid(),
        "nxbt_commit": git_commit(),
        "cpu_count": os.cpu_count(),
    }


ADAPTER = "hci-socket:0"
SAFE_MACRO = "DPAD_RIGHT 0.05s\n0.1s\nDPAD_LEFT 0.05s\n0.1s\n"


def require_bt_permission():
    from nxbt.setcap import has_cap_net_admin

    if not has_cap_net_admin():
        sys.exit(f"Needs CAP_NET_ADMIN. Run: sudo {sys.executable} {' '.join(sys.argv)}")


def start_controller(nx, reconnect=None):
    """Creates a Pro Controller on the HCI socket adapter (Bumble backend)."""
    from nxbt import PRO_CONTROLLER

    if reconnect:
        print(f"Reconnecting to {reconnect} (Switch awake, NOT on Change Grip/Order)...")
    else:
        print("Pairing: open Controllers > Change Grip/Order on the Switch...")
    return nx.create_controller(PRO_CONTROLLER, adapter_path=ADAPTER, reconnect_address=reconnect)


def poll_state(nx, idx, until=None, timeout=None, log=None, interval=0.05):
    """Polls controller state (with sleeps, unlike Nxbt.wait_for_connection).
    Appends (seconds, state) to `log` on every change. Returns the final
    state, or None on timeout."""
    start = time.perf_counter()
    last = None
    while timeout is None or time.perf_counter() - start < timeout:
        st = nx.state.get(idx, {}).get("state")
        if st != last:
            elapsed = round(time.perf_counter() - start, 2)
            print(f"  [{elapsed:7.2f}s] {st}")
            if log is not None:
                log.append((elapsed, st))
            last = st
        if st in (until, "crashed"):
            return st
        time.sleep(interval)
    return None


def interval_stats(timestamps, target_ms=TICK_TARGET_MS):
    """Summarizes the gaps (ms) between consecutive perf_counter timestamps."""
    gaps = [(b - a) * 1000 for a, b in zip(timestamps, timestamps[1:])]
    if len(gaps) < 3:
        return {"count": len(gaps)}
    ordered = sorted(gaps)

    def pct(p):
        return round(ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))], 3)

    mean = statistics.fmean(gaps)
    # Lag-1 autocorrelation: strongly negative means long/short alternation.
    dev = [g - mean for g in gaps]
    var = sum(d * d for d in dev)
    lag1 = sum(a * b for a, b in zip(dev, dev[1:])) / var if var else 0.0
    duration = timestamps[-1] - timestamps[0]
    return {
        "count": len(gaps),
        "effective_hz": round(len(gaps) / duration, 1) if duration else None,
        "target_ms": round(target_ms, 3),
        "mean_ms": round(mean, 3),
        "stdev_ms": round(statistics.pstdev(gaps), 3),
        "min_ms": round(ordered[0], 3),
        "p1_ms": pct(1),
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "max_ms": round(ordered[-1], 3),
        "within_1ms_of_target": round(sum(abs(g - target_ms) <= 1 for g in gaps) / len(gaps), 3),
        "under_1ms": round(sum(g < 1 for g in gaps) / len(gaps), 3),
        "over_20ms": sum(g > 20 for g in gaps),
        "lag1_autocorr": round(lag1, 3),
    }


class CpuSampler(threading.Thread):
    """Samples CPU % of this process and all descendants. Set `.phase` to
    label samples; `roles` maps pid -> name for known processes."""

    def __init__(self, interval=0.5):
        super().__init__(daemon=True)
        import psutil

        self._psutil = psutil
        self.interval = interval
        self.phase = "start"
        self.roles = {os.getpid(): "parent"}
        self.samples = []
        self._procs = {}
        self._stop = threading.Event()

    def run(self):
        root = self._psutil.Process()
        while not self._stop.wait(self.interval):
            try:
                procs = [root] + root.children(recursive=True)
            except self._psutil.Error:
                continue
            row = {"t": time.perf_counter(), "phase": self.phase, "procs": {}}
            for p in procs:
                tracked = self._procs.setdefault(p.pid, p)
                try:
                    cpu = tracked.cpu_percent(None)
                except self._psutil.Error:
                    continue
                row["procs"][p.pid] = round(cpu, 1)
            self.samples.append(row)

    def stop(self):
        self._stop.set()
        self.join(2)

    def summary(self):
        """Mean CPU % per (phase, role). The first sample of each process
        is skipped because psutil reports 0 until it has a baseline."""
        seen, acc = set(), {}
        for row in self.samples:
            for pid, cpu in row["procs"].items():
                if pid not in seen:
                    seen.add(pid)
                    continue
                key = (row["phase"], self.roles.get(pid, f"pid {pid}"))
                acc.setdefault(key, []).append(cpu)
        return {
            f"{phase} / {role}": round(statistics.fmean(v), 1)
            for (phase, role), v in sorted(acc.items())
        }
