#!/usr/bin/env python3

"""
Modbus/TCP -> Modbus/RTU gateway
================================

WHAT IT DOES
------------
Lets Modbus/TCP clients (SCADA, PLC, Python scripts, ...) talk to Modbus/RTU
devices wired on a serial line (RS-485 / RS-232, directly or via a USB adapter).

The script is a Modbus/TCP *server* on the network side and a Modbus/RTU
*master* on the serial side. It translates each request from one protocol to
the other and returns the answer to the client::

       network                   this modbus gateway                serial line
    --------------       ------------------------------------      -------------
      TCP client    -->  [ModbusServer]  [ModbusSerialWorker]  -->   RTU slave
                            tcp/502        to /dev/ttyUSB0

HOW A REQUEST IS PROCESSED
--------------------------
1. A TCP client sends a request. Its "unit id" (MBAP header) is used as the
   address of the target RTU slave.
2. The gateway keeps the PDU (function code + data) unchanged, adds the slave
   address in front and a CRC16 at the end: this makes the RTU frame.
3. The frame is put in a queue. A single worker sends the frames one by one on
   the serial line (an RTU bus allows only one conversation at a time) and
   waits for the slave answer.
4. The answer is checked (CRC, same slave address, same function code), the
   address and CRC are removed, and the PDU is sent back to the TCP client.

ERRORS SEEN BY THE TCP CLIENT
-----------------------------
- No valid answer from the slave before the timeout (-t): Modbus exception
  0x0B "Gateway Target Device Failed To Respond".
- More than 5 requests waiting, or serial worker unavailable: Modbus exception
  0x0A "Gateway Path Unavailable".

SERIAL PORT FAILURE
-------------------
If the serial port fails while running (e.g. USB adapter unplugged), the
gateway does NOT stop: the TCP server stays up, requests get exception 0x0B
while the port is down, and the port is reopened automatically (one attempt
per second, on incoming requests) as soon as the device is back.
The port must however be openable at startup, otherwise the gateway exits.
Exit codes: 0 normal stop (Ctrl-C), 1 serial port cannot be opened at startup,
2 Modbus server error.

LIMITATIONS
-----------
- Broadcast requests (unit id 0) are not supported: slaves never answer them,
  so the client gets exception 0x0B.
- Throughput is limited by the serial line: requests are served one at a time.
- By default the server only listens on localhost; use -H 0.0.0.0 to accept
  connections from the network.

USAGE
-----
Run this as root to listen on TCP privileged ports (<= 1024), or choose a
higher port with -p.

Open /dev/ttyUSB0 at 115200 bauds and relay Modbus/TCP requests to RTU slave(s):
$ sudo ./modbus_tcp_to_rtu_gw.py --baudrate 115200 /dev/ttyUSB0

Same, reachable from the network, 8E1 serial format, on TCP port 5020:
$ ./modbus_tcp_to_rtu_gw.py -H 0.0.0.0 -p 5020 -b 19200 --parity E /dev/ttyUSB0

Add -d (--debug) to log every RTU frame exchanged on the serial line, as a
hex dump (Tx = gateway to slave, Rx = slave to gateway), e.g.:
    DEBUG [modbus_tcp_to_rtu_gw] RTU Tx [8 bytes] 01 03 00 00 00 01 84 0a
    DEBUG [modbus_tcp_to_rtu_gw] RTU Rx [7 bytes] 01 03 02 12 34 b5 33

See --help for all options.
"""

import argparse
import logging
import queue
import struct
import sys
import time
from queue import Queue
from threading import Event
from typing import Optional, Tuple, Type

# pip install pyserial==3.5
from serial import PARITY_EVEN, PARITY_NONE, PARITY_ODD, Serial, serialutil

from pyModbusTCP.constants import (
    EXP_GATEWAY_PATH_UNAVAILABLE,
    EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND,
)
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.utils import crc16

# exceptions raised by the serial port when the device is lost (explicit type,
# so both branches below have the same type for mypy)
SERIAL_IO_ERRORS: Tuple[Type[BaseException], ...] = (serialutil.SerialException, OSError)
try:
    import termios  # POSIX only

    # termios.error is neither a SerialException nor an OSError: pyserial lets it
    # escape from reset_input_buffer()/flush() when the adapter is unplugged
    SERIAL_IO_ERRORS += (termios.error,)
except ImportError:
    pass

logger = logging.getLogger("modbus_tcp_to_rtu_gw")

# delay between two attempts to reopen a lost serial port (seconds)
RECONNECT_DELAY = 1.0
# max size of a modbus RTU frame (spec)
MAX_RTU_FRAME = 256
# pending requests accepted before "gateway overloaded" exception is returned
QUEUE_SIZE = 5
# lower bound for the end-of-frame delay: USB-serial adapters add latency
# (e.g. FTDI latency timer), so the theoretical 3.5 char time is too short
MIN_EOF_DELAY = 0.02


def hexdump(data: bytes) -> str:
    """Format a raw frame for logs, like "[8 bytes] 01 03 00 00 00 01 84 0a"."""
    return '[%d bytes] %s' % (len(data), data.hex(' '))


def t35(baudrate: int, bytesize: int, parity: str, stopbits: float) -> float:
    """Return the modbus RTU inter-frame silent interval (3.5 chars) in seconds."""
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

    @property
    def pdu(self) -> bytes:
        """Return PDU part of frame."""
        return self.raw[1:-2]

    @property
    def slave_address(self) -> int:
        """Return slave address part of frame."""
        return self.raw[0]

    @property
    def function_code(self) -> int:
        """Return function code part of frame."""
        return self.raw[1]

    @property
    def is_valid(self) -> bool:
        """Check if frame is valid (minimum length and CRC).

        :return: True if frame is valid
        :rtype: bool
        """
        return len(self.raw) > 4 and crc16(self.raw) == 0

    def is_response_to(self, request: 'ModbusRTUFrame') -> bool:
        """Check that frame is a valid response matching the given request.

        Guards against late/stray frames from another slave or another
        function (the bus is shared and a slave may answer after our timeout).
        """
        return (self.is_valid
                and self.slave_address == request.slave_address
                # exception responses set the MSB of the function code
                and (self.function_code & 0x7F) == request.function_code)

    def build(self, raw_pdu: bytes, slave_ad: int) -> None:
        """Build a full modbus RTU message from PDU and slave address.

        :param raw_pdu: modbus as raw value
        :type raw_pdu: bytes
        :param slave_ad: address of the slave
        :type slave_ad: int
        """
        # [address] + PDU
        tmp_raw = struct.pack('B', slave_ad) + raw_pdu
        # [address] + PDU + [CRC 16]
        tmp_raw += struct.pack('<H', crc16(tmp_raw))
        self.raw = tmp_raw


class RtuQuery:
    """Request container to deal with modbus serial worker."""

    def __init__(self) -> None:
        self.completed = Event()
        self.request = ModbusRTUFrame()
        self.response = ModbusRTUFrame()


class ModbusSerialWorker:
    """A serial worker to manage I/O with RTU devices."""

    def __init__(self, port: Serial, timeout: float = 1.0, end_of_frame: Optional[float] = None) -> None:
        # public
        self.serial_port = port
        self.timeout = timeout
        # silent interval between two frames on the bus
        self.interframe_delay = t35(port.baudrate, port.bytesize, port.parity, port.stopbits)
        # end of frame delay: auto-computed from baudrate if not set
        if end_of_frame is None:
            end_of_frame = max(self.interframe_delay, MIN_EOF_DELAY)
        self.end_of_frame = end_of_frame
        # internal request queue
        # accept QUEUE_SIZE simultaneous requests before overloaded exception is return
        self.rtu_queries_q: 'Queue[RtuQuery]' = Queue(maxsize=QUEUE_SIZE)
        # worker stop flag
        self._stop = Event()
        self._last_bus_activity = 0.0
        self._last_reopen_try = 0.0

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
        return True

    def stop(self) -> None:
        """Ask the worker loop to exit."""
        self._stop.set()

    def _transaction(self, request: bytes) -> bytes:
        """Send a request frame on the serial line and return the raw answer.

        :return: raw frame received, empty if no answer before timeout
        :raises serialutil.SerialException: on serial I/O error
        """
        port = self.serial_port
        # respect the silent interval between frames
        idle = self._last_bus_activity + self.interframe_delay - time.monotonic()
        if idle > 0:
            time.sleep(idle)
        # send to serial
        port.reset_input_buffer()
        port.write(request)
        port.flush()
        logger.debug('RTU Tx %s', hexdump(request))
        # wait for the first byte of data until timeout delay
        port.timeout = self.timeout
        rx_raw = port.read(1)
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
        self._last_bus_activity = time.monotonic()
        if rx_raw:
            logger.debug('RTU Rx %s', hexdump(rx_raw))
        else:
            logger.debug('RTU Rx [0 bytes] timeout, no answer')
        return rx_raw

    def loop(self) -> None:
        """Serial worker main loop (returns after stop() is called)."""
        while not self._stop.is_set():
            # get next exchange from queue (periodically wake up to check the stop flag)
            try:
                rtu_query = self.rtu_queries_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if self._ensure_port_open():
                    rtu_query.response.raw = self._transaction(rtu_query.request.raw)
                else:
                    # port lost and not yet back: fail fast, the client gets a modbus exception
                    rtu_query.response.raw = b''
            except SERIAL_IO_ERRORS as e:
                # typically the USB adapter was unplugged (errno 5, Input/output error).
                # Never stop: close the port, the client gets a modbus exception
                # and the port is reopened automatically on a later request
                logger.error('Serial I/O error: %r (port %s)', e, self.serial_port.port)
                rtu_query.response.raw = b''
                self._close_port()
            except Exception:  # noqa: BLE001
                # safety net: the worker must survive, or every client would hang
                logger.exception('Unexpected error in serial worker')
                rtu_query.response.raw = b''
            finally:
                # mark all as done (always, or the TCP client thread would hang)
                rtu_query.completed.set()
                self.rtu_queries_q.task_done()
        # on exit, release anyone still waiting
        self._drain_queue()

    def _drain_queue(self) -> None:
        while True:
            try:
                rtu_query = self.rtu_queries_q.get_nowait()
            except queue.Empty:
                return
            rtu_query.completed.set()
            self.rtu_queries_q.task_done()

    def srv_engine_entry(self, session_data: ModbusServer.SessionData) -> None:
        """Server engine entry point (pass request to serial worker queries queue).

        :param session_data: server session data
        :type session_data: ModbusServer.SessionData
        """
        # init a serial exchange from session data
        rtu_query = RtuQuery()
        rtu_query.request.build(raw_pdu=session_data.request.pdu.raw,
                                slave_ad=session_data.request.mbap.unit_id)
        try:
            # add a request in the serial worker queue, can raise queue.Full
            self.rtu_queries_q.put(rtu_query, block=False)
            # wait result (bounded: worst case every queued request times out)
            wait_max = (QUEUE_SIZE + 1) * (self.timeout + self.end_of_frame) + 1.0
            if not rtu_query.completed.wait(timeout=wait_max):
                logger.error('Serial worker not responding (unit %d)', rtu_query.request.slave_address)
                exp_status = EXP_GATEWAY_PATH_UNAVAILABLE
            # check receive frame status
            elif rtu_query.response.is_response_to(rtu_query.request):
                session_data.response.pdu.raw = rtu_query.response.pdu
                return
            else:
                # except status for slave failed to respond
                logger.debug('No valid answer from unit %d', rtu_query.request.slave_address)
                exp_status = EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
        except queue.Full:
            # except status for overloaded gateway
            logger.warning('Gateway overloaded, request rejected')
            exp_status = EXP_GATEWAY_PATH_UNAVAILABLE
        # return modbus exception
        func_code = rtu_query.request.function_code
        session_data.response.pdu.build_except(func_code=func_code, exp_status=exp_status)


def main() -> int:
    # parse args
    parser = argparse.ArgumentParser(description='Modbus/TCP to Modbus/RTU gateway')
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='serial rate (default is 9600)')
    parser.add_argument('--bytesize', type=int, choices=(7, 8), default=8, help='data bits (default is 8)')
    parser.add_argument('--parity', choices=('N', 'E', 'O'), default='N', help='parity (default is N)')
    parser.add_argument('--stopbits', type=float, choices=(1, 1.5, 2), default=1, help='stop bits (default is 1)')
    parser.add_argument('-t', '--timeout', type=float, default=1.0, help='timeout delay (default is 1.0 s)')
    parser.add_argument('-e', '--eof', type=float, default=None,
                        help='end of frame delay (default: computed from baudrate, min %.3f s)' % MIN_EOF_DELAY)
    parser.add_argument('-d', '--debug', action='store_true', help='set debug mode')
    args = parser.parse_args()

    # init logging
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)-20s] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

    serial_port = None
    srv = None
    serial_worker = None
    try:
        # init serial port
        logger.info('Open serial port %s at %d bauds (%d%s%s)',
                    args.device, args.baudrate, args.bytesize, args.parity, args.stopbits)
        serial_port = Serial(port=args.device, baudrate=args.baudrate, bytesize=args.bytesize,
                             parity={'N': PARITY_NONE, 'E': PARITY_EVEN, 'O': PARITY_ODD}[args.parity],
                             stopbits=args.stopbits)

        # init serial worker
        serial_worker = ModbusSerialWorker(serial_port, args.timeout, args.eof)
        logger.debug('Interframe delay %.4f s, end of frame delay %.4f s',
                     serial_worker.interframe_delay, serial_worker.end_of_frame)

        # start modbus server with custom engine
        logger.info('Start modbus server (%s, %d)', args.host, args.port)
        srv = ModbusServer(host=args.host, port=args.port, no_block=True, ext_engine=serial_worker.srv_engine_entry,)
        srv.start()

        # start serial worker loop
        logger.info('Start serial worker')
        serial_worker.loop()
        return 0

    except serialutil.SerialException as e:
        logger.critical('Serial device error: %r', e)
        return 1
    except ModbusServer.Error as e:
        logger.critical('Modbus server error: %r', e)
        return 2
    except KeyboardInterrupt:
        logger.info('Interrupted, shutting down')
        return 0
    finally:
        # clean shutdown
        if serial_worker is not None:
            serial_worker.stop()
        if srv is not None:
            try:
                srv.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug('Error while stopping server: %r', e)
        if serial_port is not None and serial_port.is_open:
            try:
                serial_port.close()
            except SERIAL_IO_ERRORS as e:
                logger.debug('Error while closing serial port: %r', e)


if __name__ == '__main__':
    sys.exit(main())
