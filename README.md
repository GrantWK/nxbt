## Motivation

Since [Brikwerk](https://github.com/Brikwerk) is no longer active on GitHub, I created this branch to maintain and continue development of the project.

Windows support has been preliminarily validated — I will update this section as time allows.

## Plans

I started this as a fork of the original project. Once it reaches sufficient maturity and independence, I plan to establish it as a standalone repository.

- [x] Clean the code
- [x] Use pyproject.toml and uv to manage the package and requirements
- [ ] Fix webapp unexpected behaviors
- [x] Use [bumble](https://github.com/google/bumble) to rewrite the repo
- [x] Add Windows support for generic USB drivers through [zadig](https://zadig.akeo.ie/), such as [WinUSB](https://learn.microsoft.com/en-us/windows-hardware/drivers/usbcon/introduction-to-winusb-for-developers)
- [ ] Add native GUI for webapp using pywebview
- [ ] Add Android support

## Quick Start

Install into a virtual environment (Python 3.10+):

```sh
python3 -m venv .venv
.venv/bin/pip install .
sudo .venv/bin/nxbt webapp -i 127.0.0.1
```

Or build and run the Docker image (Linux hosts only; containers cannot reach the
Bluetooth adapter through Docker Desktop on macOS/Windows):

```sh
docker build -t nxbt -f docker/gnu/Dockerfile .
docker run --rm -it --network host --cap-add=NET_ADMIN nxbt webapp -i 127.0.0.1
```

`--network host` is required because Linux only allows Bluetooth sockets in the
host network namespace. `-i 127.0.0.1` keeps the web app off other interfaces;
it binds to `0.0.0.0` by default.

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
  ExecStart=/path/to/.venv/bin/nxbt webapp -i 127.0.0.1
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

## Contributions Welcome

Everyone is welcome to share ideas or contribute through issues and pull requests.

## Thanks

Many thanks to the original author [Brikwerk](https://github.com/Brikwerk).

## More

The original readme can be found [here](https://github.com/typenoob/nxbt/blob/master/README.old.md)
