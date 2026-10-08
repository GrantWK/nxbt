# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Based on [typenoob/nxbt](https://github.com/typenoob/nxbt) `develop` at
`b647484`, itself a fork of [Brikwerk/nxbt](https://github.com/Brikwerk/nxbt) `ec4b800`.

## [Unreleased]

### Security
- Never set file capabilities on the Python interpreter. Previously every
  `nxbt` run as root called `setcap cap_net_admin,cap_net_bind_service+eip`
  on `/proc/self/exe`; outside a compiled binary that is the system Python
  (e.g. `/usr/bin/python3.11`), which would give those capabilities to every
  Python program on the machine. `set_file_cap` now refuses anything that is
  not a compiled nxbt binary.

### Fixed
- A controller no longer crashes when the Switch sleeps longer than the
  reconnect attempts (`PSM already in use`). It now keeps retrying with a
  growing delay (up to 5 s) until the Switch wakes, and only re-pairs when
  the Switch rejects the bond. Also fixes crashes on unhandled reconnect
  errors, a pairing timeout, and controller removal mid-reconnect, and a
  re-pair saving nxbt's own address instead of the Switch's.
- Running nxbt as root no longer rewrites the `bluetoothd` systemd override
  and restarts `bluetoothd` regardless of backend. That disconnected other
  Bluetooth devices and left `bluetoothd` running without plugins until
  reboot when using Bumble. Only the BlueZ backend manages the override now.
- Permission checks now read the process's effective capabilities
  (`/proc/self/status`) instead of file capabilities on the executable, so
  running as root, under systemd `AmbientCapabilities`, or in Docker with
  `--cap-add=NET_ADMIN` is detected correctly.
- Wheels and non-editable installs now include all subpackages
  (`nxbt.backends`, `nxbt.backends.internal`, `nxbt.controller.sdp`) and the
  web app's templates and static files. Previously only the top-level
  package was packaged, so a pip-installed nxbt could not import its backends.
- systemd detection reads `/proc/1/comm` instead of calling
  `ps --no-headers`, which crashed on BusyBox (Alpine).
- `tests/test_bluez.py` no longer hardcodes a developer's home directory and
  loads `bluez.py` with its package context so relative imports resolve.

### Changed
- Install with plain `pip install .`; the Docker images are now multi-stage
  pip installs on official `debian:stable-slim` and `alpine:3.21` images.
- The Docker CI workflow builds and pushes the gnu and musl images only.
- `uv.lock` resolves from pypi.org.

### Removed
- Nuitka single-binary builds: the `nuitka` dev dependency, Makefile build
  targets, `nuitka-project` directives in `nxbt/__main__.py`, the MSYS2
  Dockerfile, and binary release steps in CI.
- The Tsinghua PyPI mirror as the default uv index, and the DaoCloud
  Docker registry mirror.
