"""The Modbus/RTU side shared by the two gateways (mbtcp-tcp2rtu, mbtcp-rtu2tcp): frames, timings, serial errors.

Needs pyserial, like the gateways (pip install "pyModbusTCP[serial]").
"""

import logging
import struct
import sys
import time

from ._common import BROADCAST_ADDRESS, MAX_SLAVE_ADDRESS, SERIAL_MISSING_MSG

try:
    from serial import PARITY_NONE, Serial, serialutil
except ModuleNotFoundError as _exc:
    # pyserial is optional: give the way to install it instead of a bare "No module named 'serial'"
    if _exc.name != 'serial':
        raise
    raise ModuleNotFoundError(SERIAL_MISSING_MSG, name='serial') from _exc

from ..utils import crc16

# exceptions raised by the serial port when the device is lost (explicit type,
# so both branches below have the same type for mypy)
SERIAL_IO_ERRORS: tuple[type[BaseException], ...] = (serialutil.SerialException, OSError)
# a sys.platform test (not a try/except ImportError) so that mypy knows termios is POSIX only
if sys.platform != 'win32':
    import termios

    # termios.error is neither a SerialException nor an OSError: pyserial lets it
    # escape from reset_input_buffer()/flush() when the adapter is unplugged
    SERIAL_IO_ERRORS += (termios.error,)

# delay between two attempts to reopen a lost serial port (seconds)
RECONNECT_DELAY = 1.0
# max size of a modbus RTU frame (spec)
MAX_RTU_FRAME = 256
# min size of a RTU frame: slave address, function code, CRC16 (a request like "read exception status", 0x07); a
# response has at least one more byte (data, or the code of an exception)
MIN_RTU_FRAME = 4
# lower bound for the end-of-frame delay: USB-serial adapters add latency
# (e.g. FTDI latency timer), so the theoretical 3.5 char time is too short
MIN_EOF_DELAY = 0.02
# BROADCAST_ADDRESS (0) and MAX_SLAVE_ADDRESS (247): from _common (without pyserial, for the argparse types)
__all__ = ['BROADCAST_ADDRESS', 'MAX_RTU_FRAME', 'MAX_SLAVE_ADDRESS', 'MIN_EOF_DELAY', 'MIN_RTU_FRAME',
           'RECONNECT_DELAY', 'SERIAL_IO_ERRORS', 'ModbusRTUFrame', 'SerialLink', 'hexdump', 't35']


def hexdump(data: bytes) -> str:
    """Format a raw frame for logs, like "[8 bytes] 01 03 00 00 00 01 84 0a"."""
    return f"[{len(data)} bytes] {data.hex(' ')}"


def t35(baudrate: int, bytesize: int, parity: str, stopbits: float) -> float:
    """Return the modbus RTU inter-frame silent interval (3.5 chars) in seconds."""
    if baudrate <= 0:
        raise ValueError(f'invalid baud rate: {baudrate}')
    if baudrate > 19200:
        # fixed value recommended by the modbus serial line spec
        return 0.00175
    bits_per_char = 1 + bytesize + (0 if parity == PARITY_NONE else 1) + stopbits
    return 3.5 * bits_per_char / baudrate


class ModbusRTUFrame:
    """A Modbus/RTU frame: slave address, PDU (function code first), CRC16 (little endian)."""

    def __init__(self, raw: bytes = b'') -> None:
        self.raw = raw

    def __repr__(self) -> str:
        return hexdump(self.raw)

    @property
    def pdu(self) -> bytes:
        """The PDU of the frame (without slave address and CRC)."""
        return self.raw[1:-2]

    @property
    def slave_address(self) -> int:
        """The slave address of the frame."""
        return self.raw[0]

    @property
    def function_code(self) -> int:
        """The function code of the frame."""
        return self.raw[1]

    @property
    def is_set(self) -> bool:
        """True if the frame is not empty."""
        return bool(self.raw)

    @property
    def is_valid(self) -> bool:
        """True if the frame has the min size of a frame (MIN_RTU_FRAME) and a good CRC."""
        return len(self.raw) >= MIN_RTU_FRAME and crc16(self.raw) == 0

    def build(self, raw_pdu: bytes, slave_address: int) -> None:
        """Build the frame of a PDU for a slave address: [address] + PDU + [CRC16]."""
        raw = struct.pack('B', slave_address) + raw_pdu
        self.raw = raw + struct.pack('<H', crc16(raw))


class SerialLink:
    """The serial line of a gateway: its timings, and a port reopened when it is lost (USB adapter unplugged...).

    :param port: the serial port (open)
    :param end_of_frame: the silence that ends a frame (s), None for the 3.5 chars of the baud rate (at least
        MIN_EOF_DELAY)
    :param log: the logger of the gateway
    """

    def __init__(self, port: Serial, end_of_frame: float | None, log: logging.Logger) -> None:
        self.serial_port = port
        # silent interval between two frames on the bus
        self.interframe_delay = t35(port.baudrate, port.bytesize, port.parity, port.stopbits)
        # end of frame delay: auto-computed from baudrate if not set
        if end_of_frame is None:
            end_of_frame = max(self.interframe_delay, MIN_EOF_DELAY)
        self.end_of_frame = end_of_frame
        self._log = log
        self._last_reopen_try = 0.0

    def _close_port(self) -> None:
        """Close the serial port after an I/O error (never raises)."""
        try:
            self.serial_port.close()
        except SERIAL_IO_ERRORS as e:
            self._log.debug('Error while closing serial port: %r', e)
        # first reopen attempt after RECONNECT_DELAY
        self._last_reopen_try = time.monotonic()

    def _ensure_port_open(self) -> bool:
        """Reopen the serial port if it was lost (rate limited).

        :return: True if the port is usable
        """
        port = self.serial_port
        if port.is_open:
            return True
        now = time.monotonic()
        if now - self._last_reopen_try < RECONNECT_DELAY:
            return False
        self._last_reopen_try = now
        try:
            port.open()
        except SERIAL_IO_ERRORS as e:
            self._log.warning('Unable to reopen serial port %s: %r', port.port, e)
            return False
        self._log.info('Serial port %s reopened', port.port)
        self._port_reopened()
        return True

    def _port_reopened(self) -> None:
        """Called after the port is reopened (a hook for the gateways)."""
