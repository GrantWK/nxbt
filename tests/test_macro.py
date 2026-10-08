"""Macro parsing and lazy step iteration, including LOOP FOREVER."""

import copy
import tracemalloc
from itertools import islice
from unittest.mock import MagicMock

import pytest

from nxbt.controller.input import DIRECT_INPUT_IDLE_PACKET, InputParser
from nxbt.controller.macro import parse_macro
from nxbt.library import BUILTIN_DIR


def steps(text, limit=None):
    cursor = parse_macro(text)
    out = []
    while cursor and (limit is None or len(out) < limit):
        out.append(cursor.pop())
    return out


def test_plain_steps_skip_blanks_and_comments():
    assert steps("A 0.1s\n\n# comment\n0.1s\n") == ["A 0.1s", "0.1s"]


def test_loop_repeats_body():
    assert steps("X 1s\nLOOP 2\n    A 0.1s\n    0.1s\nY 1s\n") == [
        "X 1s", "A 0.1s", "0.1s", "A 0.1s", "0.1s", "Y 1s",
    ]


def test_nested_and_sequential_loops():
    text = "LOOP 2\n    A 1s\n    LOOP 2\n        B 1s\nLOOP 1\n    C 1s\n"
    assert steps(text) == ["A 1s", "B 1s", "B 1s", "A 1s", "B 1s", "B 1s", "C 1s"]


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
    cursor = parse_macro("START 1s\nLOOP FOREVER\n    A 0.4s\n    0.05s\n")
    assert cursor.remaining is None
    produced = [cursor.pop() for _ in range(1001)]
    assert produced[0] == "START 1s"
    assert produced[1:] == ["A 0.4s", "0.05s"] * 500
    assert cursor  # still going


def test_remaining_counts_down_for_finite_macros():
    cursor = parse_macro("LOOP 3\n    A 1s\n    0.1s\n")
    assert cursor.remaining == 6
    cursor.pop()
    assert cursor.remaining == 5


@pytest.mark.parametrize("line", ["LOOP", "LOOP x", "LOOP -1", "LOOP 2 3"])
def test_invalid_loop_counts_raise(line):
    with pytest.raises(ValueError):
        parse_macro(f"{line}\n    A 1s\n")


def test_huge_loops_are_not_expanded_in_memory():
    body = "".join(f"    STEP{i} 0.1s\n" for i in range(14))
    tracemalloc.start()
    cursor = parse_macro("5s\nLOOP 100000\n" + body)
    first = list(islice(iter(cursor.pop, None), 3))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert cursor.remaining == 1_400_001 - 3
    assert first == ["5s", "STEP0 0.1s", "STEP1 0.1s"]
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
