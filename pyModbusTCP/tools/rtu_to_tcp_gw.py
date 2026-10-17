"""
mbtcp-rtu2tcp: Modbus/RTU -> Modbus/TCP gateway
===============================================

Run it as "mbtcp-rtu2tcp" (pyModbusTCP-cli package) or "python -m pyModbusTCP.tools rtu2tcp".
It needs pyserial: pip install "pyModbusTCP[serial]" (installed with pyModbusTCP-cli).

WHAT IT DOES
------------
Lets a Modbus/RTU master (PLC, HMI, data logger, ... wired on a serial line:
RS-485 / RS-232, directly or via a USB adapter) talk to a Modbus/TCP server
available on the network.

The script is a Modbus/RTU *slave* on the serial side and a Modbus/TCP
*client* on the network side. It translates each request from one protocol to
the other and returns the answer to the master. It is the reverse of
mbtcp-tcp2rtu::

     serial line              this modbus gateway                     network
    -------------      -----------------------------------       -----------------
     RTU master   -->  [ModbusSerialWorker] [ModbusClient]  -->  Modbus/TCP server
                        from /dev/ttyUSB0    to tcp/502

HOW A REQUEST IS PROCESSED
--------------------------
1. The gateway listens on the serial line and collects a frame: it ends when
   the line has been silent for the end of frame delay (-e).
2. The frame is dropped if its CRC is wrong (as any RTU slave would do).
3. The frame is processed only if it is addressed to the slave address of the
   gateway (-a), or to address 0 (broadcast) when --allow-broadcast is set.
   Frames for other addresses are ignored: they are meant for other slaves
   of the same bus.
4. The PDU (function code + data) is kept unchanged and sent to the Modbus/TCP
   server (with unit id -u).
5. The server answer PDU is wrapped in an RTU frame (slave address in front,
   CRC16 at the end) and written on the serial line.
   A broadcast request is relayed to the server but never answered.

ERRORS SEEN BY THE RTU MASTER
-----------------------------
- The Modbus/TCP server answers with a Modbus exception: the same exception
  code is returned to the master.
- The Modbus/TCP server is unreachable or doesn't answer before the timeout
  (-t): exception 0x0B "Gateway Target Device Failed To Respond".

SERIAL PORT FAILURE
-------------------
If the serial port fails while running (e.g. USB adapter unplugged), the
gateway does NOT stop: it closes the port and tries to reopen it every second
until the device is back.
The port must however be openable at startup, otherwise the gateway exits.
Exit codes: 0 normal stop (Ctrl-C or SIGTERM), 1 serial port cannot be opened at
startup, 2 invalid parameter.

LIMITATIONS
-----------
- The gateway acts as one single RTU slave: it answers to one address only (-a).
- Requests are served one at a time, as the serial line is.

USAGE
-----
Open /dev/ttyUSB0 at 115200 bauds and relay RTU requests for slave address 30
to the Modbus/TCP server running on localhost:502:
$ mbtcp-rtu2tcp --baudrate 115200 --address 30 /dev/ttyUSB0

Same, with a server on the network (unit id 1), 8E1 serial format:
$ mbtcp-rtu2tcp -b 19200 --parity E -a 30 -H 192.168.1.10 -p 502 /dev/ttyUSB0

Add -d (--debug) to log every RTU frame exchanged on the serial line, as a
hex dump (Rx = master to gateway, Tx = gateway to master), e.g.:
    DEBUG [mbtcp-rtu2tcp       ] RTU Rx [8 bytes] 1e 03 00 00 00 01 8f 8d (CRC OK)
    DEBUG [mbtcp-rtu2tcp       ] RTU Tx [7 bytes] 1e 03 02 12 34 4d f6

See --help for all options.
"""

import argparse
import logging
import struct
import sys
import time
from threading import Event

from ._common import (
    SERIAL_MISSING_MSG,
    add_version_arg,
    positive_float,
    positive_int,
    slave_address,
    stop_on_sigterm,
    tcp_port,
)

try:
    from serial import PARITY_EVEN, PARITY_NONE, PARITY_ODD, Serial, serialutil
except ModuleNotFoundError as _exc:
    # pyserial is optional: give the way to install it instead of a bare "No module named 'serial'"
    if _exc.name != 'serial':
        raise
    raise ModuleNotFoundError(SERIAL_MISSING_MSG, name='serial') from _exc

from ..client import ModbusClient
from ..constants import (
    EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND,
    MB_EXCEPT_ERR,
)
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

logger = logging.getLogger("mbtcp-rtu2tcp")

# delay between two attempts to reopen a lost serial port (seconds)
RECONNECT_DELAY = 1.0
# max size of a modbus RTU frame (spec)
MAX_RTU_FRAME = 256
# how long to wait for the start of a frame before checking the stop flag (seconds)
IDLE_POLL_DELAY = 0.5
# lower bound for the end-of-frame delay: USB-serial adapters add latency
# (e.g. FTDI latency timer), so the theoretical 3.5 char time is too short
MIN_EOF_DELAY = 0.02


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
    """Modbus RTU frame container class."""

    def __init__(self, raw: bytes = b'') -> None:
        # public
        self.raw = raw

    def __repr__(self) -> str:
        return hexdump(self.raw)

    @property
    def pdu(self) -> bytes:
        """Return PDU part of frame."""
        return self.raw[1:-2]

    @property
    def slave_addr(self) -> int:
        """Return slave address part of frame."""
        return self.raw[0]

    @property
    def function_code(self) -> int:
        """Return function code part of frame."""
        return self.raw[1]

    @property
    def is_set(self) -> bool:
        """Check if frame is set

        :return: True if frame is set
        :rtype: bool
        """
        return bool(self.raw)

    @property
    def is_valid(self) -> bool:
        """Check if frame is valid (minimum length and CRC).

        A request frame can be as short as 4 bytes (address, function code, CRC16),
        like "read exception status" (function 0x07).

        :return: True if frame is valid
        :rtype: bool
        """
        return len(self.raw) >= 4 and crc16(self.raw) == 0

    def build(self, raw_pdu: bytes, slave_addr: int) -> None:
        """Build a full modbus RTU message from PDU and slave address.

        :param raw_pdu: modbus as raw value
        :type raw_pdu: bytes
        :param slave_addr: address of the slave
        :type slave_addr: int
        """
        # [address] + PDU
        tmp_raw = struct.pack('B', slave_addr) + raw_pdu
        # [address] + PDU + [CRC 16]
        tmp_raw += struct.pack('<H', crc16(tmp_raw))
        self.raw = tmp_raw


class SlaveSerialWorkerModel:
    """A base serial worker to manage I/O loop with RTU master device."""

    def __init__(self, port: Serial, end_of_frame: float | None = None) -> None:
        # public
        self.serial_port = port
        # silent interval between two frames on the bus
        self.interframe_delay = t35(port.baudrate, port.bytesize, port.parity, port.stopbits)
        # end of frame delay: auto-computed from baudrate if not set
        if end_of_frame is None:
            end_of_frame = max(self.interframe_delay, MIN_EOF_DELAY)
        self.end_of_frame = end_of_frame
        self.request = ModbusRTUFrame()
        self.response = ModbusRTUFrame()
        # worker stop flag
        self._stop = Event()
        self._last_reopen_try = 0.0
        # input buffer must be flushed at start and after each port reopen
        self._flush_pending = True

    def handle_request(self) -> None:
        """Template method to be implemented by subclasses for custom request logic."""
        raise NotImplementedError('Subclasses must implement handle_request')

    def stop(self) -> None:
        """Ask the worker loop to exit."""
        self._stop.set()

    def _close_port(self) -> None:
        """Close the serial port after an I/O error (never raises)."""
        try:
            self.serial_port.close()
        except SERIAL_IO_ERRORS as e:
            logger.debug('Error while closing serial port: %r', e)
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
            logger.warning('Unable to reopen serial port %s: %r', port.port, e)
            return False
        logger.info('Serial port %s reopened', port.port)
        self._flush_pending = True
        return True

    def _receive_frame(self) -> bytes:
        """Wait for a frame on the serial line.

        :return: raw frame received, empty if nothing received yet (idle poll delay elapsed)
        :raises serialutil.SerialException: on serial I/O error
        """
        port = self.serial_port
        # wait for the first byte of data (periodically wake up to check the stop flag)
        port.timeout = IDLE_POLL_DELAY
        rx_raw: bytes = port.read(1)
        # if ok, wait for the remaining until the line is silent for end of frame delay.
        # Note: pyserial ignores inter_byte_timeout on POSIX, and read(n) waits for n bytes
        # or the full timeout, so read what is already buffered (or 1 byte) in a loop.
        if rx_raw:
            port.timeout = self.end_of_frame
            while len(rx_raw) < MAX_RTU_FRAME:
                rx_chunk = port.read(min(max(port.in_waiting, 1), MAX_RTU_FRAME - len(rx_raw)))
                if not rx_chunk:
                    break
                rx_raw += rx_chunk
        return rx_raw

    def run(self) -> None:
        """Serial worker process loop (returns after stop() is called)."""
        while not self._stop.is_set():
            try:
                # lost port: wait a bit and retry (never stop)
                if not self._ensure_port_open():
                    self._stop.wait(RECONNECT_DELAY)
                    continue
                # flush serial buffer
                if self._flush_pending:
                    self.serial_port.reset_input_buffer()
                    self._flush_pending = False
                # receive from serial
                rx_raw = self._receive_frame()
                if not rx_raw:
                    continue
                # init a new transaction
                self.request = ModbusRTUFrame(rx_raw)
                self.response = ModbusRTUFrame()
                crc_ok = self.request.is_valid
                # log of received items for debugging purposes
                logger.debug('RTU Rx %s (CRC %s)', hexdump(rx_raw), 'OK' if crc_ok else 'ERROR')
                # just ignore current frame on CRC error
                if not crc_ok:
                    continue
                # relay PDU of request to modbus server
                self.handle_request()
                # if a response frame is set sent it
                if self.response.is_set:
                    self.serial_port.write(self.response.raw)
                    self.serial_port.flush()
                    # log sent items for debugging purposes
                    logger.debug('RTU Tx %s', hexdump(self.response.raw))
            except SERIAL_IO_ERRORS as e:
                # typically the USB adapter was unplugged (errno 5, Input/output error).
                # Never stop: close the port, it is reopened automatically
                logger.error('Serial I/O error: %r (port %s)', e, self.serial_port.port)
                self._close_port()
            except Exception:
                # safety net: the worker must survive
                logger.exception('Unexpected error in serial worker')


class SerialModbusWorker(SlaveSerialWorkerModel):
    """A specialized serial worker that maps RTU requests to a Modbus TCP client."""

    def __init__(self, port: Serial, mbus_cli: ModbusClient, slave_addr: int = 1, allow_bcast: bool = False,
                 end_of_frame: float | None = None) -> None:
        # 0 is the broadcast address, 248 to 255 are reserved: the gateway would never answer
        if not 1 <= slave_addr <= 247:
            raise ValueError('slave address must be between 1 and 247')
        super().__init__(port, end_of_frame=end_of_frame)
        self.mbus_cli = mbus_cli
        self.slave_addr = slave_addr
        self.allow_bcast = allow_bcast

    def _relay_to_server(self, pdu: bytes) -> tuple[bytes | None, int]:
        """Send a PDU to the Modbus/TCP server.

        :return: tuple (response PDU or None if error, modbus exception status to use on error)
        """
        cli = self.mbus_cli
        try:
            resp_pdu = cli.custom_request(pdu)
        except Exception:
            # not a serial error: never let it reach the serial error handler
            logger.exception('Unexpected error with Modbus/TCP client')
            return None, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
        if resp_pdu:
            return resp_pdu, 0
        logger.warning('Modbus/TCP request failed: %s', cli.last_error_as_txt)
        # the server answered with a modbus exception: pass it as is to the master
        if cli.last_error == MB_EXCEPT_ERR and cli.last_except:
            return None, cli.last_except
        # network error, timeout, ...
        return None, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND

    def handle_request(self) -> None:
        """Request handler implementing the TCP relay logic."""
        req = self.request
        if req.slave_addr == 0:
            # broadcast frame
            if self.allow_bcast:
                # if config allow it, process a broadcast request (=> process it, but don't respond)
                self._relay_to_server(req.pdu)
            else:
                logger.debug('Broadcast frame ignored (see --allow-broadcast)')
        elif req.slave_addr == self.slave_addr:
            # process unicast request
            resp_pdu, exp_status = self._relay_to_server(req.pdu)
            if resp_pdu:
                # regular response from Modbus/TCP client
                self.response.build(raw_pdu=resp_pdu, slave_addr=req.slave_addr)
            else:
                # exception response
                exp_pdu = struct.pack('BB', (req.function_code | 0x80) & 0xFF, exp_status)
                self.response.build(raw_pdu=exp_pdu, slave_addr=req.slave_addr)
        else:
            # request for another slave of the bus
            logger.debug('Frame for slave %d ignored (gateway is slave %d)', req.slave_addr, self.slave_addr)


def main(argv: list[str] | None = None) -> int:
    # parse args
    parser = argparse.ArgumentParser(prog='mbtcp-rtu2tcp', description='Modbus/RTU to Modbus/TCP gateway')
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-a', '--address', type=slave_address, default=1,
                        help='slave address of the gateway, 1 to 247 (default: 1)')
    parser.add_argument('--allow-broadcast', action='store_true', help='relay broadcast frames (address 0) to the server')
    parser.add_argument('-b', '--baudrate', type=positive_int, default=9600, help='serial rate (default: 9600)')
    parser.add_argument('--bytesize', type=int, choices=(7, 8), default=8, help='data bits (default: 8)')
    parser.add_argument('--parity', choices=('N', 'E', 'O'), default='N', help='parity (default: N)')
    parser.add_argument('--stopbits', type=float, choices=(1, 1.5, 2), default=1, help='stop bits (default: 1)')
    parser.add_argument('-e', '--eof', type=positive_float, default=None,
                        help='serial end of frame delay in s (default: computed from baudrate, '
                             f'min {MIN_EOF_DELAY:.3f} s)')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='server host (default: localhost)')
    parser.add_argument('-p', '--port', type=tcp_port, default=502, help='server TCP port (default: 502)')
    parser.add_argument('-u', '--unit-id', type=int, default=1, help='server unit id (default: 1)')
    parser.add_argument('-t', '--timeout', type=positive_float, default=1.0,
                        help='server timeout delay in s (default: 1.0)')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode')
    add_version_arg(parser)
    args = parser.parse_args(argv)

    # init logging
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)-20s] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

    serial_port = None
    mbus_cli = None
    serial_worker = None
    try:
        # init serial port
        logger.info('Open serial port %s at %d bauds (%d%s%s)',
                    args.device, args.baudrate, args.bytesize, args.parity, args.stopbits)
        serial_port = Serial(port=args.device, baudrate=args.baudrate, bytesize=args.bytesize,
                             parity={'N': PARITY_NONE, 'E': PARITY_EVEN, 'O': PARITY_ODD}[args.parity],
                             stopbits=args.stopbits)

        # start modbus client as request relay
        logger.info('Connect to modbus server at %s:%d, unit id %d (timeout = %.2fs)',
                    args.host, args.port, args.unit_id, args.timeout)
        mbus_cli = ModbusClient(host=args.host, port=args.port, unit_id=args.unit_id, timeout=args.timeout)

        # init specialized serial worker
        serial_worker = SerialModbusWorker(port=serial_port, mbus_cli=mbus_cli, slave_addr=args.address,
                                           allow_bcast=args.allow_broadcast, end_of_frame=args.eof)
        logger.debug('Interframe delay %.4f s, end of frame delay %.4f s',
                     serial_worker.interframe_delay, serial_worker.end_of_frame)

        # start serial worker loop (until Ctrl-C or SIGTERM)
        logger.info('Start serial worker (slave address = %d)', args.address)
        with stop_on_sigterm(serial_worker.stop, logger):
            serial_worker.run()
        return 0

    except serialutil.SerialException as e:
        logger.critical('Serial device error: %r', e)
        return 1
    except ValueError as e:
        logger.critical('Invalid parameter: %s', e)
        return 2
    except KeyboardInterrupt:
        logger.info('Interrupted, shutting down')
        return 0
    finally:
        # clean shutdown
        if serial_worker is not None:
            serial_worker.stop()
        if mbus_cli is not None:
            mbus_cli.close()
        if serial_port is not None and serial_port.is_open:
            try:
                serial_port.close()
            except SERIAL_IO_ERRORS as e:
                logger.debug('Error while closing serial port: %r', e)


if __name__ == '__main__':
    sys.exit(main())
