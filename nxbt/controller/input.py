import logging
from time import perf_counter
from json import dumps

from .macro import parse_macro


DIRECT_INPUT_IDLE_PACKET = {
    # Sticks
    "L_STICK": {
        "PRESSED": False,
        "X_VALUE": 0,
        "Y_VALUE": 0,
        # Keyboard position calculation values
        "LS_UP": False,
        "LS_LEFT": False,
        "LS_RIGHT": False,
        "LS_DOWN": False,
    },
    "R_STICK": {
        "PRESSED": False,
        "X_VALUE": 0,
        "Y_VALUE": 0,
        # Keyboard position calculation values
        "RS_UP": False,
        "RS_LEFT": False,
        "RS_RIGHT": False,
        "RS_DOWN": False,
    },
    # Dpad
    "DPAD_UP": False,
    "DPAD_LEFT": False,
    "DPAD_RIGHT": False,
    "DPAD_DOWN": False,
    # Triggers
    "L": False,
    "ZL": False,
    "R": False,
    "ZR": False,
    # Joy-Con Specific Buttons
    "JCL_SR": False,
    "JCL_SL": False,
    "JCR_SR": False,
    "JCR_SL": False,
    # Meta buttons
    "PLUS": False,
    "MINUS": False,
    "HOME": False,
    "CAPTURE": False,
    # Buttons
    "Y": False,
    "X": False,
    "B": False,
    "A": False,
}


class InputParser:
    # Left Stick calibration values
    LEFT_STICK_CALIBRATION = {
        "center_x": 2159,
        "center_y": 1916,
        # Zeroed Min/Max X and Y
        "min_x": -1466,
        "max_x": 1517,
        "min_y": -1583,
        "max_y": 1465,
    }
    # Right Stick calibration values
    RIGHT_STICK_CALIBRATION = {
        "center_x": 2070,
        "center_y": 2013,
        # Zeroed Min/Max X and Y
        "min_x": -1522,
        "max_x": 1414,
        "min_y": -1531,
        "max_y": 1510,
    }

    def __init__(self, protocol):
        self.protocol = protocol

        # Buffers a list of unparsed macros
        self.macro_buffer = []

        # Keeps track of the entire current
        # list of macro commands.
        self.current_macro = None
        self.current_macro_id = None
        # Keeps track of the macro commands being
        # input over a period of time.
        self.current_macro_commands = None

        # The time length of the current macro
        self.macro_timer_length = 0

        # The start time for the current macro commands
        self.macro_timer_start = 0
        # Whether the current step has been applied to at least one report
        self._step_applied = False

        self.controller_input = None

    def buffer_macro(self, macro, macro_id):
        # Doesn't have any info
        if len(macro) < 4:
            return

        self.macro_buffer.append([macro, macro_id])

    def stop_macro(self, macro_id, state=None):
        # Check if the macro is being input currently
        if macro_id == self.current_macro_id:
            # If so, reset the current macro
            self.current_macro = None
            self.current_macro_id = None
            self.current_macro_commands = None
            self.macro_timer_length = 0
            self.macro_timer_start = 0
        else:
            # Check if the macro is still in the buffer
            for i in range(0, len(self.macro_buffer)):
                if macro_id == self.macro_buffer[i][1]:
                    del self.macro_buffer[i]

        # Ensure the stopped macro is added to the finished
        # macros so that any blocking parties listening can
        # continue.
        if state:
            finished = state["finished_macros"]
            finished.append(macro_id)
            state["finished_macros"] = finished

        return

    def clear_macros(self):
        self.current_macro = None
        self.current_macro_id = None
        self.current_macro_commands = None
        self.macro_timer_length = 0
        self.macro_timer_start = 0
        self.macro_buffer = []

        return

    def set_controller_input(self, controller_input):
        self.controller_input = controller_input

    def commands_queued(self):
        check = dumps(self.controller_input) != dumps(DIRECT_INPUT_IDLE_PACKET)
        check = check or self.macro_buffer
        check = check or self.current_macro
        check = check or self.current_macro_commands
        return check

    def active_input_queued(self):
        """Checks if an active command input is queued. An active command
        is a depressed button or tilted stick.

        :return: True (on an active button) or False (no active buttons)
        :rtype: bool
        """
        if self.current_macro_commands is not None:
            if len(self.current_macro_commands) < 2:
                return False
            else:
                return True
        elif dumps(self.controller_input) != dumps(DIRECT_INPUT_IDLE_PACKET):
            return True
        else:
            return False

    def set_protocol_input(self, state=None):
        # Act on direct input if we're not getting idle packets
        if dumps(self.controller_input) != dumps(DIRECT_INPUT_IDLE_PACKET):
            self.parse_controller_input(self.controller_input)
            self.controller_input = None

        elif self.macro_buffer or self.current_macro or self.current_macro_commands:
            now = perf_counter()
            step_start = now

            # End the current step once it has been sent and its time is up.
            # The next step starts at this step's scheduled end, so late ticks
            # don't add up over a macro.
            if (
                self.current_macro_commands
                and self._step_applied
                and now - self.macro_timer_start >= self.macro_timer_length
            ):
                step_start = self.macro_timer_start + self.macro_timer_length
                self.current_macro_commands = None
                if not self.current_macro:
                    step_start = now
                    if state:
                        finished = state["finished_macros"]
                        finished.append(self.current_macro_id)
                        state["finished_macros"] = finished

            # Check if we can start on a new macro.
            if not self.current_macro and self.macro_buffer:
                macro, macro_id = self.macro_buffer.pop(0)
                try:
                    self.current_macro = parse_macro(macro)
                    self.current_macro_id = macro_id
                except ValueError as e:
                    # Skip the macro instead of crashing the controller
                    logging.getLogger("nxbt").warning(f"Skipping macro: {e}")
                    if state:
                        state["finished_macros"] = state["finished_macros"] + [macro_id]

            # Load the next step in the same tick the previous one ended
            if not self.current_macro_commands and self.current_macro:
                self.load_step(self.current_macro.pop().strip(" ").split(" "), step_start)

            if self.current_macro_commands:
                self.set_macro_input(self.current_macro_commands)
                self._step_applied = True

    def load_step(self, commands, start=None):
        """Makes `commands` (buttons/sticks, then a duration like "0.1s") the
        current macro step. Every step is sent at least once, even "0.0s"."""
        self.current_macro_commands = commands
        self.macro_timer_length = float(commands[-1][:-1])
        self.macro_timer_start = perf_counter() if start is None else start
        self._step_applied = False

    def macro_status(self):
        """What the macro engine is doing, for display; None when idle."""
        if not (self.current_macro_commands or self.current_macro or self.macro_buffer):
            return None
        return {
            "id": self.current_macro_id,
            "step": " ".join(self.current_macro_commands or []),
            # None means the macro repeats until stopped (LOOP FOREVER)
            "steps_left": self.current_macro.remaining if self.current_macro else 0,
            "queued": len(self.macro_buffer),
        }

    def next_change_at(self):
        """perf_counter() time the current macro step ends, or None."""
        if self.current_macro_commands and self._step_applied:
            return self.macro_timer_start + self.macro_timer_length
        return None

    def parse_controller_input(self, controller_input):
        # Check for input validity
        if type(controller_input) != dict:
            return

        # Arrays representing the 3 button bytes in the
        # standard input report as binary.
        upper = ["0"] * 8
        shared = ["0"] * 8
        lower = ["0"] * 8
        # Upper Byte
        if controller_input["Y"]:
            upper[7] = "1"
        if controller_input["X"]:
            upper[6] = "1"
        if controller_input["B"]:
            upper[5] = "1"
        if controller_input["A"]:
            upper[4] = "1"
        if controller_input["JCL_SR"]:
            upper[3] = "1"
        if controller_input["JCL_SL"]:
            upper[2] = "1"
        if controller_input["R"]:
            upper[1] = "1"
        if controller_input["ZR"]:
            upper[0] = "1"

        # Shared byte
        if controller_input["MINUS"]:
            shared[6] = "1"
        if controller_input["PLUS"]:
            shared[7] = "1"
        if controller_input["R_STICK"]["PRESSED"]:
            shared[5] = "1"
        if controller_input["L_STICK"]["PRESSED"]:
            shared[4] = "1"
        if controller_input["HOME"]:
            shared[3] = "1"
        if controller_input["CAPTURE"]:
            shared[2] = "1"

        # Lower byte
        if controller_input["DPAD_DOWN"]:
            lower[7] = "1"
        if controller_input["DPAD_UP"]:
            lower[6] = "1"
        if controller_input["DPAD_RIGHT"]:
            lower[5] = "1"
        if controller_input["DPAD_LEFT"]:
            lower[4] = "1"
        if controller_input["JCR_SR"]:
            lower[3] = "1"
        if controller_input["JCR_SL"]:
            lower[2] = "1"
        if controller_input["L"]:
            lower[1] = "1"
        if controller_input["ZL"]:
            lower[0] = "1"

        # Analog Stick Positions
        stick_left = self.stick_ratio_to_calibrated_position(
            controller_input["L_STICK"]["X_VALUE"] / 100,
            controller_input["L_STICK"]["Y_VALUE"] / 100,
            "L_STICK",
        )
        stick_right = self.stick_ratio_to_calibrated_position(
            controller_input["R_STICK"]["X_VALUE"] / 100,
            controller_input["R_STICK"]["Y_VALUE"] / 100,
            "R_STICK",
        )

        # Converting binary strings to ints
        upper_byte = int("".join(upper), 2)
        shared_byte = int("".join(shared), 2)
        lower_byte = int("".join(lower), 2)

        self.protocol.set_button_inputs(upper_byte, shared_byte, lower_byte)
        self.protocol.set_left_stick_inputs(stick_left)
        self.protocol.set_right_stick_inputs(stick_right)

        return controller_input

    def set_macro_input(self, macro_input):
        # Checking if this is a wait macro command
        if len(macro_input) < 2:
            return

        # Arrays representing the 3 button bytes in the
        # standard input report as binary.
        upper = ["0"] * 8
        shared = ["0"] * 8
        lower = ["0"] * 8
        # Analog stick byte placeholders
        stick_left = None
        stick_right = None
        for i in range(0, len(macro_input) - 1):
            button = macro_input[i]
            # Upper Byte
            if button == "Y":
                upper[7] = "1"
            elif button == "X":
                upper[6] = "1"
            elif button == "B":
                upper[5] = "1"
            elif button == "A":
                upper[4] = "1"
            elif button == "JCL_SR":
                upper[3] = "1"
            elif button == "JCL_SL":
                upper[2] = "1"
            elif button == "R":
                upper[1] = "1"
            elif button == "ZR":
                upper[0] = "1"

            # Shared byte
            elif button == "MINUS":
                shared[7] = "1"
            elif button == "PLUS":
                shared[6] = "1"
            elif button == "R_STICK_PRESS":
                shared[5] = "1"
            elif button == "L_STICK_PRESS":
                shared[4] = "1"
            elif button == "HOME":
                shared[3] = "1"
            elif button == "CAPTURE":
                shared[2] = "1"

            # Lower byte
            elif button == "DPAD_DOWN":
                lower[7] = "1"
            elif button == "DPAD_UP":
                lower[6] = "1"
            elif button == "DPAD_RIGHT":
                lower[5] = "1"
            elif button == "DPAD_LEFT":
                lower[4] = "1"
            elif button == "JCR_SR":
                lower[3] = "1"
            elif button == "JCR_SL":
                lower[2] = "1"
            elif button == "L":
                lower[1] = "1"
            elif button == "ZL":
                lower[0] = "1"

            # Analog Stick Positions
            elif button.startswith("L_STICK@"):
                stick_left = self.parse_macro_stick_position(button)
            elif button.startswith("R_STICK@"):
                stick_right = self.parse_macro_stick_position(button)

        # Converting binary strings to ints
        upper_byte = int("".join(upper), 2)
        shared_byte = int("".join(shared), 2)
        lower_byte = int("".join(lower), 2)

        self.protocol.set_button_inputs(upper_byte, shared_byte, lower_byte)
        if stick_left:
            self.protocol.set_left_stick_inputs(stick_left)
        if stick_right:
            self.protocol.set_right_stick_inputs(stick_right)

    def parse_macro_stick_position(self, stick_pos):
        stick_type = stick_pos.split("@")[0]
        positions = stick_pos.split("@")[1]
        if len(positions) < 8:
            return None

        # Converting macro to proper ratios
        sign_x = positions[0]
        ratio_x = int(positions[1:4]) / 100
        if sign_x == "-":
            ratio_x = ratio_x * -1

        sign_y = positions[4]
        ratio_y = int(positions[5:8]) / 100
        if sign_y == "-":
            ratio_y = ratio_y * -1

        calibrated_position = self.stick_ratio_to_calibrated_position(
            ratio_x, ratio_y, stick_type
        )

        return calibrated_position

    def stick_ratio_to_calibrated_position(self, ratio_x, ratio_y, stick_type):
        # Using the appropriate calibration values for the stick type
        if stick_type == "L_STICK":
            cal = self.LEFT_STICK_CALIBRATION
        else:
            cal = self.RIGHT_STICK_CALIBRATION

        # Converting ratios to uint16 values
        if ratio_x < 0:
            data_x_converted = abs(ratio_x) * cal["min_x"] + cal["center_x"]
        else:
            data_x_converted = abs(ratio_x) * cal["max_x"] + cal["center_x"]
        data_x_converted = int(round(data_x_converted))

        if ratio_y < 0:
            data_y_converted = abs(ratio_y) * cal["min_y"] + cal["center_y"]
        else:
            data_y_converted = abs(ratio_y) * cal["max_y"] + cal["center_y"]
        data_y_converted = int(round(data_y_converted))

        # Converting the two X/Y uint16 values to 3 uint8 Little Endian values
        # using bitshifting techniques
        converted_values = [
            # Get the last two hex digits
            data_x_converted & 0xFF,
            # Combine the last digit of the Y uint16 and the first digit
            # of the X uint16
            ((data_y_converted & 0xF) << 4) + (data_x_converted >> 8),
            # Get the first two digits of the Y uint16
            data_y_converted >> 4,
        ]

        return converted_values

    def reassign_protocol(self, protocol):
        self.protocol = protocol
