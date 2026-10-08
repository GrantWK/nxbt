import ctypes
import ctypes.util
import os

if ctypes.util.find_library("cap"):
    libcap = ctypes.CDLL(ctypes.util.find_library("cap"), use_errno=True)
    libcap.cap_from_text.argtypes = [ctypes.c_char_p]
    libcap.cap_from_text.restype = ctypes.c_void_p
    libcap.cap_set_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    libcap.cap_set_file.restype = ctypes.c_int
    libcap.cap_free.argtypes = [ctypes.c_void_p]
    libcap.cap_free.restype = ctypes.c_int
else:
    libcap = None

# Bit positions from linux/capability.h
CAP_NET_BIND_SERVICE = 10
CAP_NET_ADMIN = 12

GRANT_CAPS_HINT = 'Run `sudo env HOME="$HOME" nxbt` first to grant permissions.'


def get_effective_caps() -> int:
    """Returns the effective capability bitmask of this process, or 0 if unknown.

    This covers every way nxbt can be privileged: running as root, ambient
    capabilities from a service manager, Docker --cap-add, or file
    capabilities on a compiled binary.
    """
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return int(line.split()[1], 16)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _has_caps(*caps: int) -> bool:
    mask = get_effective_caps()
    return all(mask & (1 << cap) for cap in caps)


def has_cap_net_admin() -> bool:
    return _has_caps(CAP_NET_ADMIN)


def has_bluez_caps() -> bool:
    return _has_caps(CAP_NET_ADMIN, CAP_NET_BIND_SERVICE)


def set_file_cap(path: str, spec: str) -> None:
    if not libcap:
        return
    cap = libcap.cap_from_text(spec.encode())
    if not cap:
        raise OSError("cap_from_text failed")
    try:
        if libcap.cap_set_file(os.fsencode(path), cap) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
    finally:
        libcap.cap_free(cap)
