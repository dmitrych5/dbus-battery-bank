"""Serial port lifecycle with framed request/response reads and interference detection.

Another process (typically serial-starter probing the port) can change the serial port
settings mid-communication. Depending on the setup, such interference can affect communication
up to almost every attempt, so callers detect it via interference_detected() and reopen the
port, which restores the communication parameters. Concurrent opens can also make reads fail
outright ("device reports readiness to read but returned no data"), which no in-process
handling can ride out for long — the udev rules keeping serial-starter off the ports (see
install.sh) are therefore required; this detection remains as a safety net.
"""

import logging
import termios
import time
from struct import Struct

import serial

logger = logging.getLogger(__name__)

BIG_ENDIAN_SHORT_STRUCT = Struct(">H")
READ_POLL_SLEEP_SECONDS = 0.01

_TERMIOS_BAUD_BY_RATE = {9600: termios.B9600, 19200: termios.B19200}


class SerialLink:
    """Owns one serial device. Methods log and return None/empty on failure; retry policy
    belongs to the callers."""

    def __init__(self, device: str, baud_rate: int, timeout_seconds: float):
        if baud_rate not in _TERMIOS_BAUD_BY_RATE:
            raise ValueError(f"unsupported baud rate {baud_rate}; add its termios constant to _TERMIOS_BAUD_BY_RATE")
        self._device = device
        self._baud_rate = baud_rate
        self._timeout_seconds = timeout_seconds
        self._serial: serial.Serial | None = None

    def request(self, request_bytes: bytes, payload_length_offset: int, overhead_length: int) -> bytes | None:
        """Writes a request and reads the complete response. The response size is the payload
        length (big-endian, at payload_length_offset) plus overhead_length (everything not
        counted in the payload length: the header including the length field itself, and the
        checksum). Returns None on timeout or error."""
        port = self._ensure_open()
        if port is None:
            return None
        try:
            port.reset_input_buffer()
            port.write(request_bytes)
            data = bytearray()
            payload_length = None
            bytes_needed = payload_length_offset + BIG_ENDIAN_SHORT_STRUCT.size
            deadline = time.monotonic() + self._timeout_seconds
            while time.monotonic() < deadline:
                chunk = port.read(max(1, port.in_waiting))
                if chunk:
                    data.extend(chunk)
                # Parse the length once we have enough bytes, then read the complete message.
                if payload_length is None and len(data) >= bytes_needed:
                    payload_length = BIG_ENDIAN_SHORT_STRUCT.unpack_from(data, payload_length_offset)[0]
                    bytes_needed = payload_length + overhead_length
                if payload_length is not None and len(data) >= bytes_needed:
                    return bytes(data)
                time.sleep(READ_POLL_SLEEP_SECONDS)  # prevent busy-waiting
            return None
        except Exception:
            logger.exception("Serial request failed on %s", self._device)
            self.close()
            return None

    def read_available(self) -> bytes:
        """Reads whatever arrived, for continuously transmitting devices (VE.Direct)."""
        port = self._ensure_open()
        if port is None:
            return b""
        try:
            return port.read(port.in_waiting or 1)
        except Exception:
            logger.exception("Serial read failed on %s", self._device)
            self.close()
            return b""

    def interference_detected(self) -> bool:
        """Returns True if another process changed the serial port settings."""
        if self._serial is None:
            return False
        try:
            attributes = termios.tcgetattr(self._serial)
            expected_baud = _TERMIOS_BAUD_BY_RATE[self._baud_rate]
            return attributes[4] != expected_baud or attributes[5] != expected_baud or attributes[2] & termios.CSIZE != termios.CS8
        except Exception:
            logger.exception("Couldn't check whether there's serial connection interference")
            return False

    def reopen(self) -> None:
        self.close()
        self._ensure_open()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                logger.exception("Closing %s failed", self._device)
            self._serial = None

    def _ensure_open(self) -> serial.Serial | None:
        if self._serial is None:
            try:
                self._serial = serial.Serial(self._device, baudrate=self._baud_rate, timeout=self._timeout_seconds)
            except Exception:
                logger.exception("Couldn't open serial port %s", self._device)
        return self._serial
