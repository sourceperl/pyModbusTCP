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
     RTU master   -->  [RtuToTcpWorker]     [ModbusClient]  -->  Modbus/TCP server
                        from /dev/ttyUSB0    to tcp/502

HOW A REQUEST IS PROCESSED
--------------------------
1. The gateway listens on the serial line and collects a frame: it ends when
   the line has been silent for the end of frame delay (-e).
2. The frame is dropped if its CRC is wrong (as any RTU slave would do).
3. The frame is processed only if it is addressed to the slave address of the
   gateway (-a), or to address 0 (broadcast) when --allow-broadcast is set.
   Frames for other addresses are ignored: they are meant for other slaves
   of the same bus. So is a frame with an invalid function code (0, or 0x80
   to 0xFF: the exception flag of a reply).
   A function out of --functions gets exception 0x01 "Illegal Function" and is
   never sent to the server (see ACCESS CONTROL).
4. The PDU (function code + data) is kept unchanged and sent to the Modbus/TCP
   server (with unit id -u).
5. The server answer PDU is wrapped in an RTU frame (slave address in front,
   CRC16 at the end) and written on the serial line.
   A broadcast request is relayed to the server but never answered.

The requests to the server are sent by a relay thread, one at a time and in
the order of the bus: the gateway goes on listening to the serial line while
a broadcast is sent to the server (the master doesn't wait for it, its next
frames are received on time, never merged into one). Up to 4 broadcasts wait
for the server, the next ones are dropped (logged). A request to the gateway
waits for the broadcasts received before it, then for its own response.
A response that comes after the master has sent another frame (its timeout
has elapsed) is not written: it would collide with this frame on the bus.

ACCESS CONTROL
--------------
Any device wired on the serial line can talk to the gateway, and through it to
the Modbus/TCP server. Like mbtcp-tcp2rtu, the gateway only relays the
standard functions that read and write data by default: 0x01 to 0x06, 0x0F,
0x10, 0x16, 0x17 and read device identification 0x2B/0x0E. The others get
exception 0x01 (diagnostics 0x08, file records, the other MEI types of 0x2B,
the functions of a vendor). --functions sets this list (like --functions
1,2,3,4,6,16, or "all" with a warning at startup), --read-only only relays
the read functions (0x01 to 0x04, 0x2B/0x0E) and ignores the broadcasts.
A broadcast (with --allow-broadcast) is only relayed for a write function of
this list (0x05, 0x06, 0x0F, 0x10, 0x15, 0x16): a read without answer has no
meaning.

ERRORS SEEN BY THE RTU MASTER
-----------------------------
- The Modbus/TCP server answers with a Modbus exception: the same exception
  code is returned to the master.
- A function out of --functions (or --read-only): exception 0x01 "Illegal
  Function", from the gateway itself.
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

RS-485 MODE
-----------
An USB-RS485 adapter switches its transceiver by itself: nothing to set. The
UART of an embedded board often drives the transceiver with its RTS line:
--rs485 enables the RS-485 mode of the serial driver (Linux), the kernel
switches RTS around each frame with an exact timing. --rs485-rts-tx and
--rs485-rts-rx set the RTS levels, --rs485-delay-before-tx and
--rs485-delay-before-rx the delays around a frame. The levels are logical:
"high" is RTS asserted, a low voltage on the nRTS pin of most UARTs. A level
not given keeps the one of the driver if its RS-485 mode is already set (like
by the device tree), else it is high to send and low to receive.
The mode is applied again when a lost port is reopened. A driver without
RS-485 support refuses it at startup (exit code 2). On Windows, only the
default levels and delays are accepted; macOS has no RS-485 mode.
For a board whose transceiver sends while RTS is not asserted (nRTS pin wired
directly to the DE input):
$ mbtcp-rtu2tcp --rs485 --rs485-rts-tx low --rs485-rts-rx high -a 30 /dev/ttyRS485

Add -d (--debug) to log every RTU frame exchanged on the serial line, as a
hex dump (Rx = master to gateway, Tx = gateway to master), e.g.:
    DEBUG [mbtcp-rtu2tcp       ] RTU Rx [8 bytes] 1e 03 00 00 00 01 8f 8d (CRC OK)
    DEBUG [mbtcp-rtu2tcp       ] RTU Tx [7 bytes] 1e 03 02 12 34 4d f6

See --help for all options.
"""

import argparse
import logging
import queue
import struct
import sys
from dataclasses import dataclass, field
from threading import Event, Thread

from ._common import (
    BROADCAST_FUNCTION_CODES,
    DEFAULT_FUNCTIONS,
    READ_ONLY_FUNCTIONS,
    SERIAL_MISSING_MSG,
    FunctionFilter,
    add_rs485_args,
    add_version_arg,
    check_rs485_args,
    function_filter,
    positive_float,
    positive_int,
    setup_rs485,
    slave_address,
    stop_on_sigterm,
    tcp_port,
    unit_id,
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
    EXP_ILLEGAL_FUNCTION,
    MB_EXCEPT_ERR,
    MODBUS_PORT,
)
from ..server._common import LogThrottle
from ._rtu import (
    BROADCAST_ADDRESS,
    MAX_RTU_FRAME,
    MAX_SLAVE_ADDRESS,
    MIN_EOF_DELAY,
    RECONNECT_DELAY,
    SERIAL_IO_ERRORS,
    ModbusRTUFrame,
    SerialLink,
    hexdump,
)

logger = logging.getLogger("mbtcp-rtu2tcp")

# how long to wait for the start of a frame before checking the stop flag (seconds)
IDLE_POLL_DELAY = 0.5
# max number of broadcasts waiting for the server (the next ones are dropped): a master that sends broadcasts faster
# than the server answers never builds a long backlog, and a request to the gateway never waits behind it for long
BROADCAST_BACKLOG = 4


class SlaveSerialWorkerModel(SerialLink):
    """A base serial worker to manage I/O loop with RTU master device."""

    def __init__(self, port: Serial, end_of_frame: float | None = None) -> None:
        # the serial port, the interframe and end of frame delays
        super().__init__(port, end_of_frame, logger)
        self.request = ModbusRTUFrame()
        self.response = ModbusRTUFrame()
        # worker stop flag
        self._stop = Event()
        # input buffer must be flushed at start and after each port reopen
        self._flush_pending = True
        # unexpected errors (a bug): one message per 10 s at most, a request flood can't flood the log
        self._error_log = LogThrottle(logger, level=logging.ERROR)
        self._late_log = LogThrottle(logger)

    def handle_request(self) -> None:
        """Template method to be implemented by subclasses for custom request logic."""
        raise NotImplementedError('Subclasses must implement handle_request')

    def stop(self) -> None:
        """Ask the worker loop to exit."""
        self._stop.set()

    def _port_reopened(self) -> None:
        # the input buffer is flushed after each reopen (and at start)
        self._flush_pending = True

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

    def _send_response(self) -> None:
        """Write the response frame (if set) on the serial line, unless the master has not waited for it."""
        if not self.response.is_set:
            return
        if self.serial_port.in_waiting:
            # the master has sent another frame while the response was prepared (its timeout is shorter than the one
            # of the server): written now, the response would collide with it on the bus, it is dropped
            self._late_log.event('Response dropped, the master has sent another frame before it: %(count)d '
                                 'response(s) (timeout of the master shorter than -t?), last %(frame)s',
                                 frame=hexdump(self.response.raw))
            return
        self.serial_port.write(self.response.raw)
        self.serial_port.flush()
        # log sent items for debugging purposes
        logger.debug('RTU Tx %s', hexdump(self.response.raw))

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
                # if a response frame is set, send it
                self._send_response()
            except SERIAL_IO_ERRORS as e:
                # typically the USB adapter was unplugged (errno 5, Input/output error).
                # Never stop: close the port, it is reopened automatically
                logger.error('Serial I/O error: %r (port %s)', e, self.serial_port.port)
                self._close_port()
            except Exception:  # noqa: BLE001
                # safety net: the worker must survive
                self._error_log.event('Unexpected error in serial worker: %(count)d error(s)', exc_info=True)


@dataclass
class _RelayJob:
    """A PDU for the relay thread: done is set once result is known (None for a broadcast: nobody waits)."""
    pdu: bytes
    done: Event | None = None
    result: tuple[bytes | None, int] = field(default=(None, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND))


class RtuToTcpWorker(SlaveSerialWorkerModel):
    """A specialized serial worker that maps RTU requests to a Modbus TCP client.

    The Modbus/TCP client is used by a relay thread only, one request at a time, in the order of the bus: the serial
    loop is never blocked by a broadcast (the master doesn't wait for it, its next frames must be received on time).
    """

    def __init__(self, port: Serial, mbus_cli: ModbusClient, slave_address: int = 1, allow_broadcast: bool = False,
                 end_of_frame: float | None = None, read_only: bool = False,
                 functions: FunctionFilter | None = None) -> None:
        # 0 is the broadcast address, 248 to 255 are reserved: the gateway would never answer
        if not 1 <= slave_address <= MAX_SLAVE_ADDRESS:
            raise ValueError(f'slave address must be between 1 and {MAX_SLAVE_ADDRESS}')
        # the relayed functions, like mbtcp-tcp2rtu: read_only is a shortcut for READ_ONLY_FUNCTIONS (and no broadcast)
        if read_only and functions is not None:
            raise ValueError('read_only and functions are exclusive (read_only is a shortcut for a functions filter)')
        if functions is not None and not isinstance(functions, FunctionFilter):
            raise TypeError('functions must be a FunctionFilter (or None for the default one)')
        super().__init__(port, end_of_frame=end_of_frame)
        self.mbus_cli = mbus_cli
        self.slave_address = slave_address
        self.allow_broadcast = allow_broadcast
        self.read_only = read_only
        self.functions = READ_ONLY_FUNCTIONS if read_only else DEFAULT_FUNCTIONS if functions is None else functions
        # requests refused by the filters: one message per 10 s at most (a master in a loop can't flood the log)
        self._refused_log = LogThrottle(logger, level=logging.INFO)
        # requests for the relay thread (None: stop), in the order of the bus; only the serial loop puts in it
        self._jobs: queue.Queue[_RelayJob | None] = queue.Queue()
        self._relay_thread: Thread | None = None
        self._bcast_dropped_log = LogThrottle(logger)

    def stop(self) -> None:
        """Ask the worker loop and the relay thread to exit."""
        super().stop()
        self._jobs.put(None)

    def run(self) -> None:
        """Serial worker process loop (returns after stop() is called), with its relay thread."""
        try:
            super().run()
        finally:
            self._jobs.put(None)
            if self._relay_thread is not None:
                # the broadcasts still waiting are sent before the stop, each one ends within the timeout of the client
                self._relay_thread.join((BROADCAST_BACKLOG + 1) * self.mbus_cli.timeout + 1.0)

    def _relay_loop(self) -> None:
        """The relay thread: send the queued PDUs to the server, one at a time."""
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                job.result = self._relay_to_server(job.pdu)
            finally:
                if job.done is not None:
                    job.done.set()

    def _submit(self, job: _RelayJob) -> None:
        """Queue a job for the relay thread (started at the first one)."""
        if self._relay_thread is None or not self._relay_thread.is_alive():
            self._relay_thread = Thread(target=self._relay_loop, name='mbtcp-rtu2tcp relay', daemon=True)
            self._relay_thread.start()
        self._jobs.put(job)

    def _relay_broadcast(self, pdu: bytes) -> None:
        """Queue a broadcast for the server, don't wait for it (dropped if BROADCAST_BACKLOG are waiting)."""
        # only this thread puts jobs: the size can only decrease between the test and the put
        if self._jobs.qsize() >= BROADCAST_BACKLOG:
            self._bcast_dropped_log.event('Broadcast dropped, the server is too slow: %(count)d broadcast(s) '
                                          '(%(backlog)d waiting)', backlog=BROADCAST_BACKLOG)
            return
        self._submit(_RelayJob(pdu))

    def _relay_and_wait(self, pdu: bytes) -> tuple[bytes | None, int]:
        """Queue a request for the server (after the broadcasts received before it), wait for its result."""
        job = _RelayJob(pdu, done=Event())
        self._submit(job)
        assert job.done is not None
        # each request waiting before it ends within the timeout of the client: check the stop flag meanwhile
        while not job.done.wait(IDLE_POLL_DELAY):
            if self._stop.is_set():
                return None, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
        return job.result

    def _relay_to_server(self, pdu: bytes) -> tuple[bytes | None, int]:
        """Send a PDU to the Modbus/TCP server.

        :return: tuple (response PDU or None if error, modbus exception status to use on error)
        """
        cli = self.mbus_cli
        try:
            resp_pdu = cli.custom_request(pdu)
        except Exception:  # noqa: BLE001
            # not a serial error: never let it reach the serial error handler
            self._error_log.event('Unexpected error with Modbus/TCP client: %(count)d error(s)', exc_info=True)
            return None, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
        if resp_pdu:
            return resp_pdu, 0
        logger.warning('Modbus/TCP request failed: %s', cli.last_error_as_txt)
        # the server answered with a modbus exception: pass it as is to the master
        if cli.last_error == MB_EXCEPT_ERR and cli.last_except:
            return None, cli.last_except
        # network error, timeout, ...
        return None, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND

    def _refused(self, reason: str) -> None:
        req = self.request
        self._refused_log.event('Frame not relayed (%(reason)s): %(count)d frame(s) (last: slave %(slave)d, function '
                                '0x%(func_code)02X)', reason=reason, slave=req.slave_address,
                                func_code=req.function_code)

    def handle_request(self) -> None:
        """Request handler implementing the TCP relay logic."""
        req = self.request
        if req.slave_address not in (BROADCAST_ADDRESS, self.slave_address):
            # request for another slave of the bus
            logger.debug('Frame for slave %d ignored (gateway is slave %d)', req.slave_address, self.slave_address)
            return
        # function codes are 1 to 127: 0 or a code with the exception flag (0x80 to 0xFF, the reply of a slave) is not a
        # request, never relayed nor answered
        if not 0x01 <= req.function_code <= 0x7F:
            self._refused('invalid function code')
            return
        if req.slave_address == BROADCAST_ADDRESS:
            # broadcast frame: never answered; relayed only with --allow-broadcast, never in read-only mode, and only
            # for a write function of the filter (no slave answers: a read has no meaning)
            if not self.allow_broadcast:
                logger.debug('Broadcast frame ignored (see --allow-broadcast)')
            elif self.read_only or req.function_code not in BROADCAST_FUNCTION_CODES \
                    or not self.functions.allows(req.pdu):
                self._refused('broadcast of a function that is not a relayed write')
            else:
                # without blocking the serial loop: the master doesn't wait, its next frames must be received on time
                self._relay_broadcast(req.pdu)
            return
        if not self.functions.allows(req.pdu):
            # a function out of --functions (or --read-only): "illegal function", never sent to the server
            self._refused('function out of --functions')
            exp_pdu = struct.pack('BB', req.function_code | 0x80, EXP_ILLEGAL_FUNCTION)
            self.response.build(raw_pdu=exp_pdu, slave_address=req.slave_address)
            return
        # unicast request for the gateway: relayed to the server
        resp_pdu, exp_code = self._relay_and_wait(req.pdu)
        if resp_pdu:
            # regular response from Modbus/TCP client
            self.response.build(raw_pdu=resp_pdu, slave_address=req.slave_address)
        else:
            # exception response
            exp_pdu = struct.pack('BB', req.function_code | 0x80, exp_code)
            self.response.build(raw_pdu=exp_pdu, slave_address=req.slave_address)


def main(argv: list[str] | None = None) -> int:
    # parse args
    parser = argparse.ArgumentParser(prog='mbtcp-rtu2tcp', description='Modbus/RTU to Modbus/TCP gateway')
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-a', '--address', type=slave_address, default=1,
                        help='slave address of the gateway, 1 to 247 (default: 1)')
    parser.add_argument('--allow-broadcast', action='store_true',
                        help='relay broadcast frames (address 0, write functions only) to the server')
    functions = parser.add_mutually_exclusive_group()
    functions.add_argument('--functions', type=function_filter, default=None, metavar='CODES',
                           help='relayed functions, separated by commas, like "1,2,3,4,6,16,0x2B/0x0E" (0x2B with '
                                'its MEI type, 0x0E if not given), or "all" (default: '
                                f'{DEFAULT_FUNCTIONS.text()}); other functions get exception 0x01')
    functions.add_argument('--read-only', action='store_true',
                           help=f'only relay read functions ({READ_ONLY_FUNCTIONS.text()}), ignore broadcasts: a '
                                'shortcut for --functions')
    parser.add_argument('-b', '--baudrate', type=positive_int, default=9600, help='serial rate (default: 9600)')
    parser.add_argument('--bytesize', type=int, choices=(7, 8), default=8, help='data bits (default: 8)')
    parser.add_argument('--parity', choices=('N', 'E', 'O'), default='N', help='parity (default: N)')
    parser.add_argument('--stopbits', type=float, choices=(1, 1.5, 2), default=1, help='stop bits (default: 1)')
    parser.add_argument('-e', '--eof', type=positive_float, default=None,
                        help='serial end of frame delay in s (default: computed from baudrate, '
                             f'min {MIN_EOF_DELAY:.3f} s)')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='server host (default: localhost)')
    parser.add_argument('-p', '--port', type=tcp_port, default=MODBUS_PORT,
                        help=f'server TCP port (default: {MODBUS_PORT})')
    parser.add_argument('-u', '--unit-id', type=unit_id, default=1, help='server unit id, 0 to 255 (default: 1)')
    parser.add_argument('-t', '--timeout', type=positive_float, default=1.0,
                        help='server timeout delay in s (default: 1.0)')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode')
    add_rs485_args(parser)
    add_version_arg(parser)
    args = parser.parse_args(argv)
    check_rs485_args(parser, args)

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
        rs485 = setup_rs485(serial_port, args)
        if rs485 is not None:
            logger.info('RS-485 mode: %s', rs485.text())

        # start modbus client as request relay
        logger.info('Connect to modbus server at %s:%d, unit id %d (timeout = %.2fs)',
                    args.host, args.port, args.unit_id, args.timeout)
        mbus_cli = ModbusClient(host=args.host, port=args.port, unit_id=args.unit_id, timeout=args.timeout)

        # init specialized serial worker
        serial_worker = RtuToTcpWorker(port=serial_port, mbus_cli=mbus_cli, slave_address=args.address,
                                       allow_broadcast=args.allow_broadcast, end_of_frame=args.eof,
                                       read_only=args.read_only, functions=args.functions)
        if serial_worker.read_only:
            logger.info('Read-only mode: only read functions are relayed')
        logger.info('Relayed functions: %s', serial_worker.functions.text())
        if serial_worker.functions.codes is None:
            logger.warning('Every function is relayed (--functions all): diagnostics, file records and vendor '
                           'functions can restart, silence or reprogram the server')
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
