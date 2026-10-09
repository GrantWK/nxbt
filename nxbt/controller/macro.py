"""Macro parsing and step iteration.

A macro is parsed once into a small tree of steps and loops, then its steps
are produced one at a time while it runs. Loops are never expanded in memory,
so ``LOOP 100000`` costs no more than ``LOOP 2``.

Loop forms: ``LOOP <count>``, ``LOOP FOREVER`` (until the macro is stopped) and
``LOOP <time>`` such as ``LOOP 30m`` or ``LOOP 1h30m`` (units s, m, h, d): a
timed loop starts another repeat only while time remains, and always finishes
the repeat it is in.
"""

import re
import time

FOREVER = None
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)([smhd])", re.IGNORECASE)
_DURATION = re.compile(r"(?:\d+(?:\.\d+)?[smhd])+", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

# A step with no time is held for this long ("A" means "A 0.1s")
DEFAULT_STEP = "0.1s"
BUTTONS = frozenset({
    "A", "B", "X", "Y", "L", "R", "ZL", "ZR", "PLUS", "MINUS", "HOME", "CAPTURE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
    "L_STICK_PRESS", "R_STICK_PRESS", "JCL_SL", "JCL_SR", "JCR_SL", "JCR_SR",
})
_STEP_TIME = re.compile(r"(\d+(?:\.\d+)?)s", re.IGNORECASE)
_STICK = re.compile(r"([LR])_STICK@([+-])(\d{3})([+-])(\d{3})", re.IGNORECASE)


class Duration(float):
    """A loop length in seconds (from LOOP 30m etc.)."""


def parse_macro(text, clock=time.perf_counter):
    """Parses macro text into a MacroSteps cursor.

    :raises ValueError: naming the line of an unknown input, a bad time or a
        bad LOOP
    """
    lines = [
        (number, line)
        for number, line in enumerate(text.split("\n"), 1)
        if line.strip() and not line.strip().startswith("#")
    ]
    return MacroSteps(_parse_block(lines), clock)


def _parse_step(number, line):
    """Checks a step line and returns it normalized: uppercase inputs and an
    explicit time, e.g. "a l_stick@+000+100" -> "A L_STICK@+000+100 0.1s"."""
    tokens = line.split()
    time_token = DEFAULT_STEP
    if _STEP_TIME.fullmatch(tokens[-1]):
        time_token = tokens.pop().lower()
    for token in tokens:
        stick = _STICK.fullmatch(token)
        if stick:
            if int(stick.group(3)) > 100 or int(stick.group(5)) > 100:
                raise ValueError(f"Line {number}: stick values go from -100 to +100 in {token!r}")
        elif token.upper() not in BUTTONS:
            raise ValueError(
                f"Line {number}: {token!r} is not a button, stick or time "
                "(e.g. A, DPAD_UP, L_STICK@+000+100, 0.5s)"
            )
    return " ".join([token.upper() for token in tokens] + [time_token])


def _parse_block(lines):
    """Turns (line number, text) pairs into a list of step strings and
    (count, body) loops.
    Loops without any steps inside are dropped, so iteration always ends or
    produces a step."""
    block = []
    i = 0
    while i < len(lines):
        number, line = lines[i]
        i += 1
        if line.split()[0].upper() != "LOOP":
            block.append(_parse_step(number, line))
            continue

        try:
            count = _loop_count(line)
        except ValueError as e:
            raise ValueError(f"Line {number}: {e}") from None
        # The loop body is the following lines indented by the delimiter the
        # first body line uses (tab, 4 spaces or 2 spaces).
        delimiter = "  "
        if i < len(lines):
            if lines[i][1].startswith("\t"):
                delimiter = "\t"
            elif lines[i][1].startswith("    "):
                delimiter = "    "
        body = []
        while i < len(lines) and lines[i][1].startswith(delimiter):
            body.append((lines[i][0], lines[i][1].replace(delimiter, "", 1)))
            i += 1
        body = _parse_block(body)
        if body and count != 0:
            block.append((count, body))
    return block


def _loop_count(line):
    parts = line.split()
    if len(parts) != 2:
        raise ValueError(
            f"Invalid loop: {line.strip()!r} (use LOOP <count>, LOOP <time> or LOOP FOREVER)"
        )
    if parts[1].upper() == "FOREVER":
        return FOREVER
    if _DURATION.fullmatch(parts[1]):
        seconds = sum(
            float(amount) * _UNIT_SECONDS[unit.lower()]
            for amount, unit in _DURATION_PART.findall(parts[1])
        )
        if seconds <= 0:
            raise ValueError(f"Invalid loop time in {line.strip()!r}")
        return Duration(seconds)
    try:
        count = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid loop count in {line.strip()!r}") from None
    if count < 0:
        raise ValueError(f"Invalid loop count in {line.strip()!r}")
    return count


def _count_steps(block):
    total = 0
    for item in block:
        if isinstance(item, str):
            total += 1
            continue
        count, body = item
        inner = _count_steps(body)
        if count is FOREVER or isinstance(count, Duration) or inner is None:
            return None
        total += count * inner
    return total


class MacroSteps:
    """Produces a parsed macro's steps one at a time.

    ``pop()`` returns the next step line, or None once the macro is finished.
    Steps are looked up only when asked for, so timed loops are checked when
    their next repeat would actually start. ``remaining`` is the number of
    steps left, or None for macros with LOOP FOREVER or timed loops.
    """

    def __init__(self, block, clock=time.perf_counter):
        # Each frame: [block, next index, repeats left (None = forever), deadline]
        self._stack = [[block, 0, 1, None]]
        self._clock = clock
        self.remaining = _count_steps(block)

    def pop(self):
        step = self._advance()
        if step is not None and self.remaining is not None:
            self.remaining -= 1
        return step

    def time_left(self):
        """Seconds until the outermost running timed loop ends, or None."""
        for frame in self._stack:
            if frame[3] is not None:
                return max(0.0, frame[3] - self._clock())
        return None

    def _advance(self):
        while self._stack:
            frame = self._stack[-1]
            block, index, repeats, deadline = frame
            if index < len(block):
                frame[1] += 1
                item = block[index]
                if isinstance(item, str):
                    return item
                count, body = item
                if isinstance(count, Duration):
                    self._stack.append([body, 0, FOREVER, self._clock() + count])
                else:
                    self._stack.append([body, 0, count, None])
                continue
            # End of this block: repeat it or return to the enclosing block
            if deadline is not None:
                again = self._clock() < deadline
            elif repeats is FOREVER:
                again = True
            else:
                frame[2] -= 1
                again = frame[2] > 0
            if again:
                frame[1] = 0
            else:
                self._stack.pop()
        return None
