"""Blocking PC suspend while nxbt has a controller."""

import os
from unittest.mock import MagicMock, patch

import pytest

from nxbt.nxbt import Nxbt
from nxbt.power import SleepInhibitor


def test_does_nothing_without_systemd_inhibit():
    inhibitor = SleepInhibitor()
    with patch("nxbt.power.shutil.which", return_value=None), patch("subprocess.Popen") as popen:
        inhibitor.acquire()
    popen.assert_not_called()
    assert not inhibitor.active


def test_blocks_sleep_only_and_is_tied_to_this_process():
    inhibitor = SleepInhibitor()
    process = MagicMock(pid=4321)
    process.poll.return_value = None
    with (
        patch("nxbt.power.shutil.which", return_value="/usr/bin/x"),
        patch("nxbt.power.subprocess.Popen", return_value=process) as popen,
    ):
        inhibitor.acquire()
        inhibitor.acquire()  # already held: no second inhibitor

    command = popen.call_args.args[0]
    assert popen.call_count == 1
    assert "--what=sleep" in command and "--mode=block" in command
    assert f"--pid={os.getpid()}" in command  # ends if nxbt dies

    with patch("nxbt.power.os.killpg") as killpg:
        inhibitor.release()
    killpg.assert_called_once()
    assert not inhibitor.active


@pytest.fixture
def nx():
    nx = Nxbt.__new__(Nxbt)
    nx._sleep_inhibitor = MagicMock()
    nx._controller_lock = MagicMock()
    nx._adapters_in_use = {}
    nx.task_queue = MagicMock()
    return nx


def test_sleep_allowed_again_only_when_last_controller_removed(nx):
    nx._controller_adapter_lookup = {0: "hci-socket:0", 1: "usb:0"}
    nx.manager_state = {}

    nx._controller_adapter_lookup.pop(0)
    nx._allow_sleep_if_idle()
    nx._sleep_inhibitor.release.assert_not_called()

    nx._controller_adapter_lookup.pop(1)
    nx._allow_sleep_if_idle()
    nx._sleep_inhibitor.release.assert_called_once()


def test_removing_a_crashed_controller_allows_sleep(nx):
    nx._controller_adapter_lookup = {0: "hci-socket:0"}
    nx.manager_state = {}  # the crashed controller already left the shared state

    with pytest.raises(ValueError):
        nx.remove_controller(0)
    nx._sleep_inhibitor.release.assert_called_once()
