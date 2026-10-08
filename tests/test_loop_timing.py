"""Controller loop timing, macro step timing and wait loops."""

import copy
import socket
import threading
import time
from unittest.mock import MagicMock, patch

from nxbt.controller.controller import ControllerTypes
from nxbt.controller.input import DIRECT_INPUT_IDLE_PACKET, InputParser
from nxbt.controller.server import TICK_PERIOD, ControllerServer
from nxbt.nxbt import Nxbt


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def parser_with_clock():
    clock = FakeClock()
    parser = InputParser(MagicMock())
    parser.set_controller_input(copy.deepcopy(DIRECT_INPUT_IDLE_PACKET))  # as the loop does
    parser.set_macro_input = MagicMock()
    return parser, clock


def test_steps_start_at_previous_scheduled_end_not_at_late_tick():
    parser, clock = parser_with_clock()
    with patch("nxbt.controller.input.perf_counter", clock):
        parser.buffer_macro("A 0.05s\nB 0.05s\n", "m1")
        parser.set_protocol_input()  # t=0: step A starts
        clock.now = 0.053  # tick arrives 3 ms late
        parser.set_protocol_input()

    assert parser.current_macro_commands == ["B", "0.05s"]
    assert parser.macro_timer_start == 0.05  # not 0.053: no drift


def test_next_step_is_applied_in_the_tick_the_previous_ends():
    parser, clock = parser_with_clock()
    with patch("nxbt.controller.input.perf_counter", clock):
        parser.buffer_macro("A 0.05s\nB 0.05s\n", "m1")
        parser.set_protocol_input()
        clock.now = 0.05
        parser.set_protocol_input()

    parser.set_macro_input.assert_called_with(["B", "0.05s"])


def test_zero_length_step_is_sent_once():
    parser, clock = parser_with_clock()
    with patch("nxbt.controller.input.perf_counter", clock):
        parser.load_step(["L", "R", "0.0s"])
        parser.set_protocol_input()

    parser.set_macro_input.assert_called_once_with(["L", "R", "0.0s"])


def test_macro_finishes_and_reports_id():
    parser, clock = parser_with_clock()
    state = {"finished_macros": []}
    with patch("nxbt.controller.input.perf_counter", clock):
        parser.buffer_macro("A 0.05s\n", "m1")
        parser.set_protocol_input(state)
        clock.now = 0.06
        parser.set_protocol_input(state)

    assert state["finished_macros"] == ["m1"]


def test_next_change_at_is_the_step_end():
    parser, clock = parser_with_clock()
    with patch("nxbt.controller.input.perf_counter", clock):
        parser.buffer_macro("A 0.05s\n", "m1")
        assert parser.next_change_at() is None
        parser.set_protocol_input()
    assert parser.next_change_at() == 0.05


def make_server():
    backend = MagicMock(address="00:00:00:00:00:00")
    return ControllerServer(
        ControllerTypes.PRO_CONTROLLER,
        backend=backend,
        state={"state": "connected", "direct_input": None, "finished_macros": []},
    )


def test_loop_ticks_at_a_steady_132_hz():
    server = make_server()
    itr, switch_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    itr.setblocking(False)
    switch_side.setblocking(False)
    ticks = []
    original = server.input.set_protocol_input

    def record(state=None):
        ticks.append(time.perf_counter())
        original(state)

    server.input.set_protocol_input = record
    loop = threading.Thread(target=server.mainloop, args=(itr, None))
    loop.start()
    time.sleep(0.5)
    server.state["state"] = "removing"
    loop.join(2)
    itr.close()
    switch_side.close()

    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    mean = sum(gaps) / len(gaps)
    assert abs(mean - TICK_PERIOD) < 0.0015
    # The old loop alternated ~0 ms and ~7.6 ms ticks
    assert sum(g < 0.001 for g in gaps) / len(gaps) < 0.05


def test_run_with_check_waits_without_spinning():
    server = make_server()
    start_cpu, start = time.process_time(), time.perf_counter()

    server.run_with_check(time.sleep, 0.3)
    cpu = time.process_time() - start_cpu
    wall = time.perf_counter() - start
    assert cpu < 0.2 * wall


def test_run_with_check_returns_none_when_removed():
    server = make_server()
    server.state["state"] = "removing"
    assert server.run_with_check(time.sleep, 5) is None


def test_wait_for_connection_sleeps_between_checks():
    nx = Nxbt.__new__(Nxbt)
    nx.manager_state = {0: {"state": "connecting"}}

    def connect(_seconds):
        nx.manager_state[0] = {"state": "connected"}

    with patch("nxbt.nxbt.time.sleep", side_effect=connect) as sleep:
        nx.wait_for_connection(0)
    sleep.assert_called()


def test_macro_status_while_running_and_idle():
    parser, clock = parser_with_clock()
    assert parser.macro_status() is None
    with patch("nxbt.controller.input.perf_counter", clock):
        parser.buffer_macro("A 0.05s\nB 0.05s\n", "m1")
        parser.set_protocol_input()

    assert parser.macro_status() == {"id": "m1", "step": "A 0.05s", "steps_left": 1, "time_left": None, "queued": 0}


def test_macro_status_published_only_on_change():
    server = make_server()
    server.state = MagicMock()
    server.input.macro_status = MagicMock(side_effect=[None, {"id": "m1"}, {"id": "m1"}, None])

    for _ in range(4):
        server._publish_macro_status()

    assert server.state.__setitem__.call_count == 2  # started, then finished
