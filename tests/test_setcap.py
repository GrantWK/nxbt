import sys
from unittest.mock import mock_open, patch

import pytest

from nxbt.setcap import (
    CAP_NET_ADMIN,
    CAP_NET_BIND_SERVICE,
    GRANT_CAPS_HINT,
    get_effective_caps,
    has_bluez_caps,
    has_cap_net_admin,
    set_file_cap,
)


def test_grant_caps_hint_message_without_compiled_binary():
    # Running from source/pip must never suggest `sudo nxbt` as a way to
    # grant capabilities, since that would target the Python interpreter.
    assert "sudo env" not in GRANT_CAPS_HINT
    assert "README" in GRANT_CAPS_HINT


def test_get_effective_caps_parses_proc_status():
    status = "Name:\tpython\nCapEff:\t0000000000001400\n"
    with patch("builtins.open", mock_open(read_data=status)):
        assert get_effective_caps() == 0x1400


def test_get_effective_caps_without_proc():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert get_effective_caps() == 0


def test_has_cap_net_admin():
    with patch("nxbt.setcap.get_effective_caps", return_value=1 << CAP_NET_ADMIN):
        assert has_cap_net_admin() is True

    with patch("nxbt.setcap.get_effective_caps", return_value=0):
        assert has_cap_net_admin() is False


def test_set_file_cap_refuses_when_not_compiled(tmp_path):
    target = tmp_path / "nxbt"
    target.touch()
    with pytest.raises(PermissionError):
        set_file_cap(str(target), "cap_net_admin+eip")


def test_set_file_cap_refuses_python_interpreter_even_when_compiled():
    with patch("nxbt.setcap.IS_COMPILED", True), patch("nxbt.setcap.libcap") as libcap:
        with pytest.raises(PermissionError):
            set_file_cap(sys.executable, "cap_net_admin+eip")
        libcap.cap_set_file.assert_not_called()


def test_grant_permissions_skips_set_file_cap_when_not_compiled():
    from nxbt import cli

    with (
        patch.object(cli, "set_file_cap") as mock_set,
        patch.object(cli.os, "geteuid", return_value=1000),
    ):
        cli._grant_permissions()
    mock_set.assert_not_called()


def test_bumble_get_available_adapters_with_permissions():
    from nxbt.backends.bumble import BumbleBackend

    with (
        patch.object(BumbleBackend, "_detect_adapters", return_value=["hci-socket:0"]),
        patch("nxbt.backends.bumble.has_cap_net_admin", return_value=True),
    ):
        result = BumbleBackend.get_available_adapters()

    assert result == {
        "adapters": ["hci-socket:0"],
        "has_permissions": True,
    }


def test_bumble_get_available_adapters_without_permissions():
    from nxbt.backends.bumble import BumbleBackend

    with (
        patch.object(BumbleBackend, "_detect_adapters", return_value=["hci-socket:0"]),
        patch("nxbt.backends.bumble.has_cap_net_admin", return_value=False),
    ):
        result = BumbleBackend.get_available_adapters()

    assert result == {"adapters": [], "has_permissions": False}


def test_has_bluez_caps():
    both = (1 << CAP_NET_ADMIN) | (1 << CAP_NET_BIND_SERVICE)
    with patch("nxbt.setcap.get_effective_caps", return_value=both):
        assert has_bluez_caps() is True

    with patch("nxbt.setcap.get_effective_caps", return_value=1 << CAP_NET_ADMIN):
        assert has_bluez_caps() is False


def test_bluez_get_available_adapters_without_override_access():
    from nxbt.backends.bluez import BlueZBackend

    with (
        patch("nxbt.backends.bluez.find_objects", return_value=["/org/bluez/hci0"]),
        patch("nxbt.backends.bluez.get_blocked_hci_indices", return_value=set()),
        patch("nxbt.backends.bluez.has_bluez_caps", return_value=True),
        patch("nxbt.backends.bluez.has_bluez_override_access", return_value=False),
    ):
        result = BlueZBackend.get_available_adapters()

    assert result == {"adapters": [], "has_permissions": False}


def test_bluez_get_available_adapters_with_permissions():
    from nxbt.backends.bluez import BlueZBackend

    with (
        patch("nxbt.backends.bluez.find_objects", return_value=["/org/bluez/hci0"]),
        patch("nxbt.backends.bluez.get_blocked_hci_indices", return_value=set()),
        patch("nxbt.backends.bluez.has_bluez_caps", return_value=True),
        patch("nxbt.backends.bluez.has_bluez_override_access", return_value=True),
    ):
        result = BlueZBackend.get_available_adapters()

    assert result == {
        "adapters": ["/org/bluez/hci0"],
        "has_permissions": True,
    }
