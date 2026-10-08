"""Macro parsing and step iteration.

A macro is parsed once into a small tree of steps and loops, then its steps
are produced one at a time while it runs. Loops are never expanded in memory,
so ``LOOP 100000`` costs no more than ``LOOP 2`` and ``LOOP FOREVER`` repeats
until the macro is stopped.
"""

FOREVER = None


def parse_macro(text):
    """Parses macro text into a MacroSteps cursor.

    :raises ValueError: if a LOOP line has an invalid count
    """
    lines = [
        line
        for line in text.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    return MacroSteps(_parse_block(lines))


def _parse_block(lines):
    """Turns lines into a list of step strings and (count, body) loops.
    Loops without any steps inside are dropped, so iteration always ends or
    produces a step."""
    block = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.startswith("LOOP"):
            block.append(line)
            continue

        count = _loop_count(line)
        # The loop body is the following lines indented by the delimiter the
        # first body line uses (tab, 4 spaces or 2 spaces).
        delimiter = "  "
        if i < len(lines):
            if lines[i].startswith("\t"):
                delimiter = "\t"
            elif lines[i].startswith("    "):
                delimiter = "    "
        body = []
        while i < len(lines) and lines[i].startswith(delimiter):
            body.append(lines[i].replace(delimiter, "", 1))
            i += 1
        body = _parse_block(body)
        if body and count != 0:
            block.append((count, body))
    return block


def _loop_count(line):
    parts = line.split()
    if len(parts) != 2:
        raise ValueError(f"Invalid loop: {line.strip()!r} (use LOOP <count> or LOOP FOREVER)")
    if parts[1] == "FOREVER":
        return FOREVER
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
        if count is FOREVER or inner is None:
            return None
        total += count * inner
    return total


class MacroSteps:
    """Produces a parsed macro's steps one at a time.

    Truthy while steps remain; ``pop()`` returns the next step line.
    ``remaining`` is the number of steps left, or None if the macro repeats
    forever.
    """

    def __init__(self, block):
        # Each frame: [block, next index, iterations left (None = forever)]
        self._stack = [[block, 0, 1]]
        self.remaining = _count_steps(block)
        self._next = self._advance()

    def __bool__(self):
        return self._next is not None

    def pop(self):
        step = self._next
        if step is None:
            raise IndexError("pop from finished macro")
        if self.remaining is not None:
            self.remaining -= 1
        self._next = self._advance()
        return step

    def _advance(self):
        while self._stack:
            frame = self._stack[-1]
            block, index, _ = frame
            if index < len(block):
                frame[1] += 1
                item = block[index]
                if isinstance(item, str):
                    return item
                count, body = item
                self._stack.append([body, 0, count])
                continue
            # End of this block: repeat it or return to the enclosing block
            if frame[2] is not FOREVER:
                frame[2] -= 1
            if frame[2] is FOREVER or frame[2] > 0:
                frame[1] = 0
            else:
                self._stack.pop()
        return None
