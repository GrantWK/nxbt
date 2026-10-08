"""Turns recorded live input into macro text.

The web app records each change of its direct input packet (the same packet it
sends to the controller) with a timestamp. Each recorded state becomes one
macro step held until the next change.
"""

from .macro import parse_macro

# Button names in the order they're written in a step
BUTTONS = (
    "A", "B", "X", "Y", "L", "R", "ZL", "ZR",
    "PLUS", "MINUS", "HOME", "CAPTURE",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
    "JCL_SL", "JCL_SR", "JCR_SL", "JCR_SR",
)
STICK_STEP = 5  # stick positions are rounded to this, to tame gamepad noise
MIN_STEP_MS = 20  # shorter blips are folded into the previous step
MAX_SAMPLES = 100_000


def _stick(packet, name):
    stick = packet.get(name) or {}
    tokens = []
    if stick.get("PRESSED"):
        tokens.append(f"{name}_PRESS")

    def axis(key):
        value = max(-100, min(100, float(stick.get(key) or 0)))
        return int(round(value / STICK_STEP) * STICK_STEP)

    x, y = axis("X_VALUE"), axis("Y_VALUE")
    if x or y:
        tokens.append(f"{name}@{x:+04d}{y:+04d}")
    return tokens


def packet_tokens(packet):
    """The macro tokens (buttons and stick positions) held in a packet."""
    tokens = [button for button in BUTTONS if packet.get(button)]
    return tokens + _stick(packet, "L_STICK") + _stick(packet, "R_STICK")


def _seconds(ms):
    return f"{ms / 1000:.3f}".rstrip("0").rstrip(".") + "s"


def recording_to_macro(samples, end_ms, repeat=""):
    """Converts recorded samples to macro text.

    :param samples: ``[[ms since recording started, input packet], ...]`` in
        time order, one per input change
    :param end_ms: when recording stopped, in ms since it started
    :param repeat: "" to run once, or a LOOP argument: a count ("10"), a time
        ("30m") or "FOREVER"
    :raises ValueError: on malformed samples or an invalid repeat value
    """
    if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
        raise ValueError("Invalid recording")

    # (tokens, duration in ms), merging consecutive identical states
    steps = []
    for i, sample in enumerate(samples):
        start, packet = float(sample[0]), sample[1]
        end = float(samples[i + 1][0]) if i + 1 < len(samples) else float(end_ms)
        tokens = packet_tokens(packet if isinstance(packet, dict) else {})
        duration = end - start
        if duration < 0:
            raise ValueError("Recording samples are out of order")
        if steps and (steps[-1][0] == tokens or duration < MIN_STEP_MS):
            steps[-1][1] += duration
        else:
            steps.append([tokens, duration])

    # Idle time before the first input isn't part of the macro
    while steps and not steps[0][0]:
        steps.pop(0)
    if not steps:
        raise ValueError("Nothing was recorded: press some buttons while recording")

    body = [" ".join(tokens + [_seconds(duration)]) for tokens, duration in steps]
    header = ["# Recorded macro: describe what it does.", "# Before you start: where to be first."]
    repeat = str(repeat or "").strip().upper()
    if repeat:
        body = [f"LOOP {repeat}"] + [f"    {line}" for line in body]
    text = "\n".join(header + body) + "\n"
    parse_macro(text)  # raises ValueError for an invalid LOOP value
    return text
