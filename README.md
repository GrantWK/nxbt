# NXBT (personal fork)

A personal fork of [typenoob/nxbt](https://github.com/typenoob/nxbt), itself a continuation of
[Brikwerk/nxbt](https://github.com/Brikwerk/nxbt). It emulates a Nintendo Switch Pro Controller over
Bluetooth to automate tasks in various games, at no cost beyond a PC with Bluetooth. It is also my
learning ground for computer vision, controller emulation and the other technical pieces involved.

## Goals

- Reliable macros for different games, with no extra hardware
- A web UI suited to how I use it
- Learn computer vision (capture card + OpenCV), the Switch controller protocol, Bluetooth, and timing

Not a goal: frame-perfect input. Bluetooth cannot guarantee it, so that belongs to a separate,
wired-microcontroller project.

## Roadmap

- [x] Build hygiene: PyPI-only dependencies, pip-based Docker images, permission fixes (see [CHANGELOG](CHANGELOG.md))
- [x] Fix reconnect crashes and keep reconnecting while the Switch sleeps
- [ ] Measure Bluetooth timing on real hardware ([scripts/hw-measurements](scripts/hw-measurements))
- [ ] Fix the controller loop's timing and busy-waits
- [ ] Tests for the controller core
- [ ] Web UI rework
- [ ] Computer vision experiments

Full backlog: [GitHub Issues](https://github.com/GrantWK/nxbt/issues).

## Quick Start

Install into a virtual environment (Python 3.10+):

```sh
python3 -m venv .venv
.venv/bin/pip install .
sudo .venv/bin/nxbt webapp
```

Or build and run the Docker image (Linux hosts only; containers cannot reach the
Bluetooth adapter through Docker Desktop on macOS/Windows):

```sh
docker build -t nxbt -f docker/gnu/Dockerfile .
docker run --rm -it --network host --cap-add=NET_ADMIN nxbt webapp
```

`--network host` is required because Linux only allows Bluetooth sockets in the
host network namespace. The web app listens on `127.0.0.1` (this machine only) by
default; pass `-i <LAN IP>` to reach it from other devices. It has no login, so
only do that on a network you trust.

See [Permissions](#permissions) for running without `sudo`.

## Bluetooth Backends

NXBT supports multiple Bluetooth backend implementations. The default backend is **Bumble**, but you can switch to **BlueZ** if needed.

| Feature | BlueZ | Bumble (HCI Socket) | Bumble (USB) |
|---|---|---|---|
| **Transport** | BlueZ D-Bus API | Raw HCI socket (`/dev/hciX`) | Direct USB (libusb) |
| **Conflicts with `bluetoothd`** | Yes (shares D-Bus) | No | No |
| **HCI flow control** | Kernel-managed | Kernel-managed | Host-managed |
| **OS** | Linux | Linux | Linux / Windows |
| **Hardware** | Any kernel-supported adapter | Any kernel-supported adapter | Any USB Bluetooth dongle |

**Recommendation:** Use the HCI Socket backend when available — it avoids conflicts with system Bluetooth services and requires no additional hardware setup.

**Note:** Most modern laptops use built-in USB-based Bluetooth adapters, so the Bumble (USB) backend will work out of the box. You can verify this with `lsusb`.

## Permissions

nxbt requires privileged access to interact with Bluetooth hardware. Running the entire process as root is the simplest approach, but it is **strongly discouraged** for security reasons. Below are safer alternatives that grant only the minimum required capabilities.

### Required capabilities

| Backend | Capabilities | Why |
|---|---|---|
| **Bumble (HCI socket)** | `cap_net_admin` | Binding to raw HCI sockets (`HCI_CHANNEL_USER`) |
| **Bumble (USB)** | None (if libusb works) | Direct USB communication, no kernel socket needed |
| **BlueZ** | `cap_net_admin`, `cap_net_bind_service` | Binding to raw HCI sockets (`HCI_CHANNEL_CONTROL`), Binding to L2CAP PSM |

nxbt checks the *effective* capabilities of its own process, so any of these work:

- **systemd service (recommended for the web app):** run as an unprivileged user and grant only what is needed:
  ```ini
  [Service]
  User=nxbt
  ExecStart=/path/to/.venv/bin/nxbt webapp
  AmbientCapabilities=CAP_NET_ADMIN
  CapabilityBoundingSet=CAP_NET_ADMIN
  ```
  Add `CAP_NET_BIND_SERVICE` to both lines for the BlueZ backend.
- **Docker:** `--cap-add=NET_ADMIN` (see [Quick Start](#quick-start)).
- **Bumble (USB):** no capabilities; the user only needs read/write access to the dongle's USB device node (e.g. via a udev rule).
- **`sudo`:** works, but the whole process runs as root.

**Do not `setcap` the Python interpreter.** nxbt runs under Python, so file capabilities would
land on the shared interpreter (a venv's `python` is a symlink to it) and every Python program on
the system would inherit them. nxbt refuses to do this itself.

### BlueZ backend: systemd override

When using the BlueZ backend, nxbt needs to restart `bluetoothd` with all plugins disabled (`--noplugin=*`). This requires writing a systemd drop-in override at `/run/systemd/system/bluetooth.service.d/nxbt.conf`.

You can set this up once as root before running nxbt:

```bash
# Create the override
sudo bash -c '
mkdir -p /run/systemd/system/bluetooth.service.d
cat > /run/systemd/system/bluetooth.service.d/nxbt.conf << "EOF"
[Service]
ExecStart=
ExecStart=bluetoothd --noplugin=*
EOF
systemctl daemon-reload
systemctl restart bluetooth
'
```

Once the override file exists, nxbt will skip writing it on subsequent runs.

**Note:** when the BlueZ backend runs as root, it creates this override on start and removes it
on exit, restarting `bluetoothd` each time. The Bumble backends never touch `bluetoothd`.

## Thanks

Many thanks to the original author [Brikwerk](https://github.com/Brikwerk) and to
[typenoob](https://github.com/typenoob) for the Bumble rewrite this fork builds on.

## More

The original readme can be found in [README.old.md](README.old.md).
