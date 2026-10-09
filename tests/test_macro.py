"""Macro parsing and lazy step iteration, including LOOP FOREVER."""

import copy
import tracemalloc
from itertools import islice
from unittest.mock import MagicMock, patch

import pytest

from nxbt.controller.input import DIRECT_INPUT_IDLE_PACKET, InputParser
from nxbt.controller.macro import parse_macro
from nxbt.library import BUILTIN_DIR


def steps(text):
    cursor = parse_macro(text)
    return list(iter(cursor.pop, None))


def test_plain_steps_skip_blanks_and_comments():
    assert steps("A 0.1s\n\n# comment\n0.1s\n") == ["A 0.1s", "0.1s"]


def test_loop_repeats_body():
    assert steps("X 1s\nLOOP 2\n    A 0.1s\n    0.1s\nY 1s\n") == [
        "X 1s", "A 0.1s", "0.1s", "A 0.1s", "0.1s", "Y 1s",
    ]


def test_nested_and_sequential_loops():
    text = "LOOP 2\n    A 1s\n    LOOP 2\n        B 1s\nLOOP 1\n    X 1s\n"
    assert steps(text) == ["A 1s", "B 1s", "B 1s", "A 1s", "B 1s", "B 1s", "X 1s"]


@pytest.mark.parametrize("indent", ["\t", "  ", "    "])
def test_loop_indentation_styles(indent):
    assert steps(f"LOOP 2\n{indent}A 1s\n") == ["A 1s", "A 1s"]


def test_comments_inside_loops_are_ignored():
    assert steps("LOOP 2\n    # note\n    A 1s\n") == ["A 1s", "A 1s"]


def test_empty_and_zero_loops_produce_no_steps():
    assert steps("LOOP 0\n    A 1s\nB 1s\nLOOP FOREVER\n") == ["B 1s"]


def test_forever_with_only_empty_inner_loop_ends():
    assert steps("LOOP FOREVER\n    LOOP 0\n        A 1s\nB 1s\n") == ["B 1s"]


def test_loop_forever_repeats_until_stopped():
    cursor = parse_macro("HOME 1s\nLOOP FOREVER\n    A 0.4s\n    0.05s\n")
    assert cursor.remaining is None
    produced = [cursor.pop() for _ in range(1001)]
    assert produced[0] == "HOME 1s"
    assert produced[1:] == ["A 0.4s", "0.05s"] * 500
    assert cursor.pop() is not None  # still going


def test_remaining_counts_down_for_finite_macros():
    cursor = parse_macro("LOOP 3\n    A 1s\n    0.1s\n")
    assert cursor.remaining == 6
    cursor.pop()
    assert cursor.remaining == 5


@pytest.mark.parametrize("line", ["LOOP", "LOOP x", "LOOP -1", "LOOP 2 3", "LOOP 5x", "LOOP 0s", "LOOP 1h-5m"])
def test_invalid_loop_counts_raise(line):
    with pytest.raises(ValueError):
        parse_macro(f"{line}\n    A 1s\n")


def test_huge_loops_are_not_expanded_in_memory():
    body = "".join(f"    A {i + 1}s\n" for i in range(14))
    tracemalloc.start()
    cursor = parse_macro("5s\nLOOP 100000\n" + body)
    first = list(islice(iter(cursor.pop, None), 3))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert cursor.remaining == 1_400_001 - 3
    assert first == ["5s", "A 1s", "A 2s"]
    assert peak < 200_000  # was ~22 MB when loops were expanded


def test_library_macros_parse():
    for path in BUILTIN_DIR.glob("*/*.txt"):
        assert parse_macro(path.read_text(encoding="utf-8")), path.name


def make_parser():
    parser = InputParser(MagicMock())
    parser.set_controller_input(copy.deepcopy(DIRECT_INPUT_IDLE_PACKET))
    parser.set_macro_input = MagicMock()
    return parser


def test_invalid_macro_is_skipped_and_marked_finished():
    parser = make_parser()
    state = {"finished_macros": []}
    parser.buffer_macro("LOOP x\n    A 1s\n", "bad")
    parser.buffer_macro("B 1s\n", "good")

    parser.set_protocol_input(state)  # skips "bad"
    parser.set_protocol_input(state)  # starts "good"

    assert state["finished_macros"] == ["bad"]
    parser.set_macro_input.assert_called_with(["B", "1s"])


def test_status_reports_forever_as_none():
    parser = make_parser()
    parser.buffer_macro("LOOP FOREVER\n    A 0.4s\n    0.05s\n", "m1")
    parser.set_protocol_input()
    assert parser.macro_status()["steps_left"] is None


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.parametrize("text, seconds", [
    ("90s", 90), ("30m", 1800), ("2h", 7200), ("1d", 86400), ("1h30m", 5400), ("1.5h", 5400), ("30M", 1800),
])
def test_timed_loop_lengths(text, seconds):
    clock = FakeClock()
    cursor = parse_macro(f"LOOP {text}\n    A 1s\n", clock)
    cursor.pop()
    assert cursor.time_left() == seconds


def test_timed_loop_finishes_the_repeat_it_started():
    clock = FakeClock()
    cursor = parse_macro("LOOP 10s\n    A 3s\n    B 3s\nY 0.1s\n", clock)
    produced = []
    while (step := cursor.pop()) is not None:
        produced.append(step)
        clock.now += float(step.split()[-1][:-1])  # each step takes its time

    # Repeats start at 0 s and 6 s; the third would start at 12 s > 10 s
    assert produced == ["A 3s", "B 3s", "A 3s", "B 3s", "Y 0.1s"]
    assert cursor.remaining is None


def test_engine_does_not_rush_after_a_stall():
    clock = [0.0]
    parser = make_parser()
    applied = []
    parser.set_macro_input = lambda cmds: applied.append(" ".join(cmds))
    with patch("nxbt.controller.input.perf_counter", lambda: clock[0]):
        parser.buffer_macro("LOOP FOREVER\n    A 0.4s\n    0.05s\n", "m")
        parser.set_protocol_input()
        clock[0] += 10.0  # e.g. a reconnect
        for _ in range(40):  # the next ~0.3 s of ticks
            clock[0] += 1 / 132
            parser.set_protocol_input()

    changes = sum(a != b for a, b in zip(applied, applied[1:]))
    assert changes <= 2  # was 39: every missed step got one tick


def test_status_reports_time_left_for_timed_loops():
    parser = make_parser()
    parser.buffer_macro("LOOP 30m\n    A 0.4s\n    0.05s\n", "m1")
    parser.set_protocol_input()
    status = parser.macro_status()
    assert status["steps_left"] is None
    assert 1790 < status["time_left"] <= 1800


def test_step_without_time_uses_default():
    assert steps("HOME\nA L_STICK@+000+100\n0.5s\n") == [
        "HOME 0.1s", "A L_STICK@+000+100 0.1s", "0.5s",
    ]


def test_steps_are_normalized_to_uppercase():
    assert steps("a 0.2S\nloop 2\n    dpad_up\n") == ["A 0.2s", "DPAD_UP 0.1s", "DPAD_UP 0.1s"]


@pytest.mark.parametrize("text, line", [
    ("HOM\n", 1),  # typo
    ("A\nB 0.1x\n", 2),  # bad time
    ("# comment\n\nL_STICK@+150+000\n", 3),  # stick out of range
    ("A\nLOOP 2\n    B\n    Q 1s\n", 4),  # error inside a loop
])
def test_invalid_steps_name_their_line(text, line):
    with pytest.raises(ValueError, match=f"^Line {line}: "):
        parse_macro(text)


def test_bare_step_is_skipped_not_crashing_when_invalid():
    parser = make_parser()
    state = {"finished_macros": []}
    parser.buffer_macro("HOM\nA\n", "bad")
    parser.set_protocol_input(state)
    assert state["finished_macros"] == ["bad"]
