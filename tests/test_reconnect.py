"""Reconnect behavior when the Switch drops the link (e.g. falls asleep)."""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest
from bumble.hci import (
    HCI_AUTHENTICATION_FAILURE_ERROR,
    HCI_CONNECTION_TIMEOUT_ERROR,
    HCI_PAGE_TIMEOUT_ERROR,
    HCI_Error,
)

from nxbt.backends import BumbleBackend
from nxbt.backends.base import PairingRequired
from nxbt.controller.controller import ControllerTypes
from nxbt.controller.server import ControllerServer

SWITCH = "11:22:33:44:55:66"


@pytest.fixture(autouse=True)
def fast_backoff():
    with (
        patch("nxbt.controller.server.RECONNECT_MIN_DELAY", 0.001),
        patch("nxbt.controller.server.RECONNECT_MAX_DELAY", 0.002),
    ):
        yield


def make_server(backend):
    backend.address = "00:00:00:00:00:00"
    server = ControllerServer(
        ControllerTypes.PRO_CONTROLLER, backend=backend, state={"state": "connected"}
    )
    server.switch_address = SWITCH
    return server


def sockets():
    itr = MagicMock()
    itr.getpeername.return_value = (f"{SWITCH}/P",)
    return itr, MagicMock()


def test_retries_until_switch_wakes_without_repairing():
    backend = MagicMock()
    backend.reconnect.side_effect = [OSError("page timeout"), OSError("page timeout"), sockets()]
    server = make_server(backend)

    with patch.object(server, "pair") as pair:
        itr, _ = server.save_connection()

    assert itr is not None
    assert backend.reconnect.call_count == 3
    pair.assert_not_called()
    assert server.state["state"] == "connected"


def test_repairs_when_switch_rejects_bond():
    backend = MagicMock()
    backend.reconnect.side_effect = PairingRequired("bond rejected")
    server = make_server(backend)
    server.switch_address = "stale"

    with patch.object(server, "pair", return_value=sockets()):
        itr, _ = server.save_connection()

    assert itr is not None
    assert server.switch_address == SWITCH


def test_repair_keeps_waiting_when_pairing_returns_nothing():
    server = make_server(MagicMock())

    with patch.object(server, "pair", side_effect=[(None, None), sockets()]) as pair:
        itr, _ = server._repair()

    assert itr is not None
    assert pair.call_count == 2


def test_stops_retrying_when_controller_removed():
    backend = MagicMock()
    server = make_server(backend)

    def asleep(_address):
        server.state["state"] = "removing"
        raise OSError("page timeout")

    backend.reconnect.side_effect = asleep
    assert server.save_connection() == (None, None)


def bumble_backend():
    backend = BumbleBackend.__new__(BumbleBackend)
    backend.logger = logging.getLogger("test")
    backend._device = MagicMock(public_address=SWITCH)
    backend._loop = MagicMock()
    backend._bridges = []
    backend._l2cap_servers_device = None
    backend._transport = None  # read by __del__ cleanup
    backend._loop_thread = None
    return backend


def test_accept_registers_l2cap_servers_once():
    backend = bumble_backend()
    backend._run_async = MagicMock(return_value=MagicMock())

    with patch("nxbt.backends.bumble.asyncio.wait_for", new=MagicMock()):
        backend.accept()
        backend.accept()

    # Two PSMs (control + interrupt), registered on the first accept only
    assert backend._device.create_l2cap_server.call_count == 2


def test_accept_returns_nothing_on_timeout():
    backend = bumble_backend()
    backend._run_async = MagicMock(side_effect=asyncio.TimeoutError)

    with patch("nxbt.backends.bumble.asyncio.wait_for", new=MagicMock()):
        assert backend.accept() == (None, None)


def raising(error):
    def run_async(coro):
        coro.close()
        raise error

    return run_async


@pytest.mark.parametrize(
    "error_code", [HCI_PAGE_TIMEOUT_ERROR, HCI_CONNECTION_TIMEOUT_ERROR]
)
def test_reconnect_failures_are_retryable(error_code):
    backend = bumble_backend()
    backend._run_async = raising(HCI_Error(error_code))

    with pytest.raises(OSError) as exc:
        backend.reconnect(SWITCH)
    assert not isinstance(exc.value, PairingRequired)


def test_reconnect_rejected_bond_requires_pairing():
    backend = bumble_backend()
    backend._run_async = raising(HCI_Error(HCI_AUTHENTICATION_FAILURE_ERROR))

    with pytest.raises(PairingRequired):
        backend.reconnect(SWITCH)
