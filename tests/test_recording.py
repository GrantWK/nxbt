"""Converting recorded live input into macro text."""

import copy
import json
from unittest.mock import MagicMock, patch

import pytest

from nxbt.controller.macro import parse_macro
from nxbt.controller.recording import recording_to_macro
from nxbt.nxbt import DIRECT_INPUT_PACKET


def packet(*buttons, ls=(0, 0), rs=(0, 0), ls_press=False):
    p = copy.deepcopy(DIRECT_INPUT_PACKET)
    for button in buttons:
        p[button] = True
    p["L_STICK"].update(X_VALUE=ls[0], Y_VALUE=ls[1], PRESSED=ls_press)
    p["R_STICK"].update(X_VALUE=rs[0], Y_VALUE=rs[1])
    return p


def body(text):
    return [line for line in text.splitlines() if not line.startswith("#")]


def test_presses_become_timed_steps():
    samples = [[0, packet()], [500, packet("A")], [600, packet()], [900, packet("A", "B")]]
    text = recording_to_macro(samples, end_ms=1000)

    # Idle time before the first press is dropped; idle at the end is kept
    assert body(text) == ["A 0.1s", "0.3s", "A B 0.1s"]
    assert text.startswith("# Recorded macro")


def test_sticks_are_rounded_and_signed():
    samples = [[0, packet(ls=(0, 98.4), rs=(-51, 2))], [200, packet(ls_press=True)]]
    assert body(recording_to_macro(samples, end_ms=300)) == [
        "L_STICK@+000+100 R_STICK@-050+000 0.2s",
        "L_STICK_PRESS 0.1s",
    ]


def test_repeated_states_and_short_blips_merge():
    samples = [
        [0, packet("A")],
        [100, packet("A", ls=(1, 0))],  # rounds to the same state
        [200, packet("A", ls=(40, 0))],  # 10 ms blip
        [210, packet("A")],
    ]
    assert body(recording_to_macro(samples, end_ms=300)) == ["A 0.3s"]


@pytest.mark.parametrize("repeat, loop", [("10", "LOOP 10"), ("30m", "LOOP 30M"), ("forever", "LOOP FOREVER")])
def test_repeat_wraps_in_a_loop(repeat, loop):
    text = recording_to_macro([[0, packet("A")]], end_ms=100, repeat=repeat)
    assert body(text) == [loop, "    A 0.1s"]
    assert parse_macro(text).pop() == "A 0.1s"


@pytest.mark.parametrize("samples, end, repeat", [
    ([[0, packet()]], 100, ""),  # nothing pressed
    ([[0, packet("A")]], 100, "sometimes"),  # invalid repeat
    ([[100, packet("A")], [50, packet()]], 200, ""),  # out of order
    ("not a list", 0, ""),
])
def test_invalid_recordings(samples, end, repeat):
    with pytest.raises((ValueError, TypeError)):
        recording_to_macro(samples, end, repeat)


def test_web_returns_recorded_macro_or_error():
    from nxbt.web.app import WebApp

    app = WebApp(nxbt=MagicMock(), library=MagicMock())
    with patch.object(app, "_emit_to") as emit:
        app.handle_recording_to_macro("sid", json.dumps({"samples": [[0, packet("A")]], "end": 100}))
        app.handle_recording_to_macro("sid", json.dumps({"samples": [[0, packet()]], "end": 100}))

    assert [call.args[1] for call in emit.call_args_list] == ["recorded_macro", "library_error"]
