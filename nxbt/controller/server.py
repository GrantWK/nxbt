import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, wait
from multiprocessing import Event
import contextlib
import signal
import time
import queue
import logging
import traceback
import statistics as stat

from ..backends import BACKENDS
from ..backends.base import PairingRequired
from ..logger import create_logger

from .controller import ControllerTypes
from .protocol import ControllerProtocol
from .input import InputParser
from .utils import format_msg_controller, format_msg_switch

TICK_PERIOD = 1 / 132
# Longest gap between reports; matches the interval used before the loop
# timing fix (132 ticks at the old ~255 Hz rate), which is tested on hardware.
KEEPALIVE_INTERVAL = 0.5

# Backoff between reconnect attempts while the Switch is unreachable
RECONNECT_MIN_DELAY = 0.5
RECONNECT_MAX_DELAY = 5.0


class ControllerServer:
    def __init__(
        self,
        controller_type,
        backend,
        state=None,
        task_queue=None,
        lock=None,
        colour_body=None,
        colour_buttons=None,
        debug=False,
        log_to_file=False,
    ):
        self._debug = debug
        self._log_to_file = log_to_file

        self.logger = logging.getLogger("nxbt")
        # Cache logging level to increase performance on checks
        self.logger_level = self.logger.level

        if state:
            self.state = state

        self.task_queue = task_queue

        self.controller_type = controller_type
        self.colour_body = colour_body
        self.colour_buttons = colour_buttons

        self.lock = lock

        # Initializing Bluetooth
        self.backend = backend

        self.protocol = ControllerProtocol(
            self.controller_type,
            self.backend.address,
            colour_body=self.colour_body,
            colour_buttons=self.colour_buttons,
        )

        self.input = InputParser(self.protocol)

        # Debug timekeeping storage array
        self.times = []

        # Initial reconnection overload protection
        self.tick = 1
        self.cached_msg = ""
        # Last macro status written to the shared state (for the web UI)
        self._macro_status = None

    def is_running(self):
        if self.state["state"] == "removing":
            self.backend.shutdown()
            return False
        return True

    def run_with_check(self, func, *args, **kwargs):
        """Runs func in a worker thread and returns its result, or None if the
        controller is removed first. Checks for removal every 0.1 s."""
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(func, *args, **kwargs)
            while self.is_running():
                try:
                    return future.result(timeout=0.1)
                except concurrent.futures.TimeoutError:
                    continue
            return None
        finally:
            ex.shutdown(wait=False)

    def run(self, reconnect_address=None):
        """Runs the mainloop of the controller server.

        :param reconnect_address: The Bluetooth MAC address of a
        previously connected to Nintendo Switch, defaults to None
        :type reconnect_address: string or list, optional
        """
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        try:
            create_logger(debug=self._debug, log_to_file=self._log_to_file)
            self.logger = logging.getLogger("nxbt")
            self.logger_level = self.logger.level

            self.state["state"] = "initializing"

            # If we have a lock, prevent other controllers
            # from initializing at the same time and saturating the DBus,
            # potentially causing a kernel panic.
            if self.lock:
                self.lock.acquire()
            try:
                self.run_with_check(self.backend.setup, self.controller_type)
                if reconnect_address:
                    try:
                        itr, ctrl = self.reconnect(reconnect_address)
                    except OSError:
                        itr, ctrl = self.pair()
                else:
                    itr, ctrl = self.pair()
            finally:
                if self.lock:
                    self.lock.release()
            if not itr or not ctrl:
                raise RuntimeError("Cannot get bluetooth sockets")
            self.switch_address = itr.getpeername()[0].replace("/P", "")
            self.state["last_connection"] = self.switch_address
            # Clean up stale bonds on other backends so they don't
            # interfere with future connections.
            for name, backend_cls in BACKENDS.items():
                if type(self.backend) is backend_cls:
                    continue
                try:
                    backend_cls.remove_bonded_device(self.switch_address)
                except Exception as e:
                    self.logger.debug(f"Failed to remove bond from {name}: {e}")

            self.state["state"] = "connected"
            self.mainloop(itr, ctrl)

        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.state["state"] = "crashed"
            self.state["errors"] = str(e)
            self.logger.error(f"Controller crashed: {e}\n{traceback.format_exc()}")
            return self.state

    def mainloop(self, itr, ctrl):
        next_tick = last_send = time.perf_counter()
        while self.is_running():
            # Start timing command processing
            timer_start = time.perf_counter()

            # Attempt to get output from Switch
            try:
                reply = itr.recv(50)
                if len(reply) > 40:
                    elapsed = (time.perf_counter() - timer_start) * 1000
                    self.logger.debug(f"recv took {elapsed:.1f}ms, len={len(reply)}")
                    self.logger.debug(format_msg_switch(reply))
            except BlockingIOError:
                reply = None

            # Getting any inputs from the task queue
            if self.task_queue:
                try:
                    while True:
                        msg = self.task_queue.get_nowait()
                        if msg and msg["type"] == "macro":
                            self.input.buffer_macro(msg["macro"], msg["macro_id"])
                        elif msg and msg["type"] == "stop":
                            self.input.stop_macro(msg["macro_id"], state=self.state)
                        elif msg and msg["type"] == "clear":
                            self.input.clear_macros()
                except queue.Empty:
                    pass

            # Set Direct Input
            if self.state["direct_input"]:
                self.input.set_controller_input(self.state["direct_input"])
            self.protocol.process_commands(reply)
            self.input.set_protocol_input(state=self.state)

            msg = self.protocol.get_report()
            if self.logger_level <= logging.DEBUG and reply and len(reply) > 45:
                self.logger.debug(format_msg_controller(msg))

            try:
                # Cache the last packet to prevent overloading the switch
                # with packets on the "Change Grip/Order" menu.
                if msg[3:] != self.cached_msg:
                    send_start = time.perf_counter()
                    itr.sendall(msg)
                    send_elapsed = (time.perf_counter() - send_start) * 1000
                    self.logger.debug(
                        f"[send] msg len={len(msg)}, sendall took {send_elapsed:.1f}ms"
                    )
                    self.cached_msg = msg[3:]
                    last_send = send_start
                # Send a blank packet every so often to keep the Switch
                # from disconnecting from the controller.
                elif time.perf_counter() - last_send >= KEEPALIVE_INTERVAL:
                    send_start = time.perf_counter()
                    itr.sendall(msg)
                    send_elapsed = (time.perf_counter() - send_start) * 1000
                    self.logger.debug(
                        f"[send] keepalive len={len(msg)}, sendall took {send_elapsed:.1f}ms"
                    )
                    last_send = send_start
            except BlockingIOError:
                pass  # Socket busy: the unsent report is retried next tick
            except OSError:
                # Attempt to reconnect to the Switch
                itr, ctrl = self.save_connection()
                next_tick = time.perf_counter()

            self._publish_macro_status()
            next_tick = self._sleep_until_next_tick(next_tick)
            self.tick += 1

            if self.logger_level <= logging.DEBUG:
                self.times.append(time.perf_counter() - timer_start)
                if len(self.times) > 100:
                    self.times.pop(0)
                mean_time = stat.mean(self.times)

                self.logger.debug(f"Tick: {self.tick}, Mean Time: {str(1 / mean_time)}")

    def _publish_macro_status(self):
        """Shares macro progress with the web UI. Runs after the report is
        sent and only writes on change, so it never delays an input."""
        status = self.input.macro_status()
        if status != self._macro_status:
            self._macro_status = status
            self.state["macro_status"] = status

    def _sleep_until_next_tick(self, next_tick):
        """Sleeps until the next tick deadline, waking early if a macro step
        ends first so its change is sent on time. Deadlines advance by a fixed
        period, so the rate holds at 132 Hz; after a long stall the schedule
        restarts instead of running a burst of catch-up ticks.

        :return: the deadline to pass on the next call
        """
        now = time.perf_counter()
        if now >= next_tick:
            next_tick += TICK_PERIOD
            if next_tick <= now:
                next_tick = now + TICK_PERIOD
        wake = next_tick
        step_end = self.input.next_change_at()
        if step_end is not None and step_end < wake:
            wake = step_end
        if wake > now:
            time.sleep(wake - now)
        return next_tick

    def _run_pairing_handshake(self, itr):
        received_first_message = False
        while self.is_running():
            try:
                reply = itr.recv(50)
                if self.logger_level <= logging.DEBUG and len(reply) > 40:
                    self.logger.debug(format_msg_switch(reply))
            except BlockingIOError:
                reply = None

            if reply:
                received_first_message = True

            self.protocol.process_commands(reply)
            msg = self.protocol.get_report()

            if self.logger_level <= logging.DEBUG and reply:
                self.logger.debug(format_msg_controller(msg))

            try:
                itr.sendall(msg)
            except BlockingIOError:
                continue

            if (
                reply
                and len(reply) > 45
                and self.protocol.vibration_enabled
                and self.protocol.player_number
            ):
                break

            if not received_first_message:
                time.sleep(1)
            else:
                time.sleep(1 / 15)

    def save_connection(self):
        """Restores a dropped connection. Retries with growing delays while the
        Switch is unreachable (e.g. asleep) and only re-pairs when the Switch
        rejects this controller's bond.

        :return: (itr, ctrl), or (None, None) if the controller is being removed
        """
        delay = RECONNECT_MIN_DELAY
        while self.is_running():
            self._reset_protocol()
            try:
                with self._bluetooth_lock():
                    itr, ctrl = self.reconnect(self.switch_address)
                self.state["state"] = "connected"
                return itr, ctrl
            except PairingRequired as e:
                self.logger.debug(e)
                return self._repair()
            except OSError as e:
                self.logger.debug(f"Reconnect failed, retrying in {delay:.1f}s: {e}")
                self._wait(delay)
                delay = min(delay * 2, RECONNECT_MAX_DELAY)
        return None, None

    def _repair(self):
        """Waits in pairing mode until a Switch pairs, or the controller is removed."""
        self.logger.debug("Connecting to any Switch")
        self.tick = 1
        while self.is_running():
            self._reset_protocol()
            # Press the buttons the Change Grip/Order menu asks for
            if self.controller_type == ControllerTypes.PRO_CONTROLLER:
                self.input.load_step(["L", "R", "0.0s"])
            elif self.controller_type == ControllerTypes.JOYCON_L:
                self.input.load_step(["JCL_SL", "JCL_SR", "0.0s"])
            elif self.controller_type == ControllerTypes.JOYCON_R:
                self.input.load_step(["JCR_SL", "JCR_SR", "0.0s"])
            with self._bluetooth_lock():
                itr, ctrl = self.pair()
            if itr:
                self.state["state"] = "connected"
                self.switch_address = itr.getpeername()[0].replace("/P", "")
                return itr, ctrl
            self._wait(RECONNECT_MIN_DELAY)
        return None, None

    def _reset_protocol(self):
        self.protocol = ControllerProtocol(
            self.controller_type,
            self.backend.address,
            colour_body=self.colour_body,
            colour_buttons=self.colour_buttons,
        )
        self.input.reassign_protocol(self.protocol)

    def _bluetooth_lock(self):
        return self.lock if self.lock else contextlib.nullcontext()

    def _wait(self, seconds):
        """Sleeps up to `seconds`, returning early if the controller is removed."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and self.is_running():
            time.sleep(0.1)

    def pair(self):
        """Listens for and pairs with an incoming Nintendo Switch connection."""
        try:
            self.state["state"] = "connecting"
            self.logger.debug("Waiting for incoming HID connections...")
            itr, ctrl = self.run_with_check(self.backend.accept) or (None, None)
            if not itr:
                return None, None
            self.logger.debug(f"Accepted connection from {itr.getpeername()[0]}")
            itr.setblocking(False)
            self.protocol.process_commands(None)
            itr.sendall(self.protocol.get_report())
            self._run_pairing_handshake(itr)
            return itr, ctrl
        except OSError as e:
            self.logger.debug(e)
        return None, None

    def reconnect(self, reconnect_address):
        """Reconnects to a Switch at the given address.

        :param reconnect_address: The Bluetooth MAC address of the Switch
        :type reconnect_address: string or list
        """
        self.state["state"] = "reconnecting"
        result = self.run_with_check(self.backend.reconnect, reconnect_address)
        if result is None:
            raise OSError("Controller removed while reconnecting")
        itr, ctrl = result
        itr.setblocking(False)
        return itr, ctrl
