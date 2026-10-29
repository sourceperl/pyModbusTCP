"""
mbtcp-tcp2rtu: Modbus/TCP -> Modbus/RTU gateway
===============================================

Run it as "mbtcp-tcp2rtu" (pyModbusTCP-cli package) or "python -m pyModbusTCP.tools tcp2rtu".
It needs pyserial: pip install "pyModbusTCP[serial]" (installed with pyModbusTCP-cli).

WHAT IT DOES
------------
Lets Modbus/TCP clients (SCADA, PLC, Python scripts, ...) talk to Modbus/RTU
devices wired on a serial line (RS-485 / RS-232, directly or via a USB adapter).

The script is a Modbus/TCP *server* on the network side and a Modbus/RTU
*master* on the serial side. It translates each request from one protocol to
the other and returns the answer to the client::

       network                  this modbus gateway              serial line
    --------------       --------------------------------       -------------
      TCP client    -->  [ModbusServer]  [TcpToRtuWorker]  -->    RTU slave
                            tcp/5020      to /dev/ttyUSB0

HOW A REQUEST IS PROCESSED
--------------------------
1. A TCP client sends a request. Its "unit id" (MBAP header) is used as the
   address of the target RTU slave.
2. The gateway keeps the PDU (function code + data) unchanged, adds the slave
   address in front and a CRC16 at the end: this makes the RTU frame.
3. The frame is put in a queue. A single worker sends the frames one by one on
   the serial line (an RTU bus allows only one conversation at a time) and
   waits for the slave answer.
4. The answer is checked (CRC, same slave address, same function code, and a
   content consistent with the request: the size of the data read, the echo
   of a write), the address and CRC are removed, and the PDU is sent back to
   the TCP client. An RTU frame has no transaction id: a frame that is not
   the answer of the request (like a late reply of a slave to a previous
   request) is ignored, and the gateway keeps listening until the timeout.
   These frames are logged (once per 10 s at most): a slave that answers
   late needs a longer timeout (-t).

BROADCAST (unit id 0, with --allow-broadcast)
---------------------------------------------
A broadcast is a write to every slave of the bus, and no slave answers it. The
gateway queues the frame for the bus and answers the TCP client at once with
the normal response of the function (like a slave would: the echo of the
request for 0x05, 0x06, 0x15 and 0x16, the address and quantity for 0x0F and
0x10). The client never waits for a timeout. After the write, the bus stays
silent for --broadcast-delay (default 0.1 s): the slaves execute the request
before the next frame. Only these write functions can be broadcast (exception
0x01 for the others: a read has no meaning without an answer), and only if
they are relayed (0x15 is not in the default list of --functions).
A few broadcasts can wait for the bus (--broadcast-buffer, default 4), each
one in turn with the other requests of its client host. A broadcast that
waited more than --broadcast-max-age (default 10 s) is dropped, never written:
a slow or lost bus never applies old writes long after they were sent. With
the buffer full, the next broadcast gets exception 0x06 "Slave Device Busy" at
once (a flood of broadcasts never builds a long backlog).

ERRORS SEEN BY THE TCP CLIENT
-----------------------------
- Request refused by the gateway filters (see ACCESS CONTROL): Modbus exception
  0x01 "Illegal Function" (a function out of --functions or --read-only, an
  invalid function code: 0 or 0x80 to 0xFF, or a broadcast of a function
  that is not a write), 0x03 "Illegal Data Value" (a request of a standard
  function that is not exactly the size of its fields) or 0x0A "Gateway
  Path Unavailable" (unit id 0 without --allow-broadcast, or reserved unit id
  248 to 255).
- No valid answer from the slave before the timeout (-t): Modbus exception
  0x0B "Gateway Target Device Failed To Respond".
- More than 16 requests waiting, more than 2 requests of the same client host
  waiting or in progress, or serial worker unavailable: Modbus exception 0x0A
  "Gateway Path Unavailable".
- A broadcast while --broadcast-buffer broadcasts already wait for the bus:
  Modbus exception 0x06 "Slave Device Busy".

SERIAL PORT FAILURE
-------------------
If the serial port fails while running (e.g. USB adapter unplugged), the
gateway does NOT stop: the TCP server stays up, requests get exception 0x0B
while the port is down, and the port is reopened automatically (one attempt
per second, on incoming requests) as soon as the device is back.
The port must however be openable at startup, otherwise the gateway exits.
Exit codes: 0 normal stop (Ctrl-C or SIGTERM), 1 serial port cannot be opened at
startup, 2 Modbus server error or invalid parameter.

ACCESS CONTROL
--------------
Modbus has no authentication: any client that can reach the gateway can drive
every slave of the serial line. The gateway handles each request itself (it is
the fallback handler of ModbusServer), so it applies its own filters:
- By default the server only listens on localhost; use -H to listen on the
  address of the network interface that needs it (or 0.0.0.0 for all of them,
  -6 for IPv6: like -6 -H ::1, or -6 -H :: for every interface).
- -A/--allow (repeatable) restricts the clients to some IP addresses or
  networks (like -A 192.168.1.10 -A 10.0.0.0/24), others are disconnected.
- Only the standard functions that read and write data are relayed by
  default: 0x01 to 0x06, 0x0F, 0x10, 0x16, 0x17 and read device
  identification 0x2B/0x0E. The others get exception 0x01: diagnostics 0x08
  (it can restart a slave or make it silent until a power cycle), file
  records 0x14/0x15, the other MEI types of 0x2B, the functions of a vendor
  (programming, firmware...). --functions sets this list, like
  --functions 1,2,3,4,6,16 (decimal or hexadecimal, 0x2B with its MEI type
  like 0x2B/0x0E), or --functions all to relay every function (warning at
  startup).
- --read-only only relays the read functions (0x01 to 0x04 and read device
  identification 0x2B/0x0E) and refuses the broadcasts: a shortcut for
  --functions, the two options are exclusive.
- A request with an invalid function code (0, or 0x80 to 0xFF: the exception
  flag of a reply) is never written on the bus: exception 0x01.
- A request of a standard function (0x01 to 0x07, 0x0B, 0x0C, 0x0F to 0x11,
  0x14 to 0x18, 0x2B/0x0E) is written on the bus only if it has exactly the
  size of its fields (with --functions all too): exception 0x03 otherwise.
  The bytes after a request would be in the same RTU frame, and a slave that
  ends a frame by its size (instead of the silence of 3.5 chars) would take
  them for the next frame: a hidden request, to any slave, for any function
  (like a write behind a read with --read-only). The other functions
  (diagnostics 0x08, vendor functions...) have no size known to the gateway:
  only relay them to slaves that you trust with them.
- Broadcast requests (unit id 0) are a write to every slave of the bus: they
  are refused unless --allow-broadcast is set (and refused with --read-only).
  Unit ids 248 to 255 are reserved on a serial line and always refused.

FAIR SHARE OF THE SERIAL LINE
-----------------------------
The serial line serves one request at a time, and a request to a slave that
doesn't answer holds it for the whole timeout (-t). So no client host can
monopolize it, nor a few of them:
- the waiting requests are served in turn by client host (an IPv4 address, or
  a /64 network for IPv6, see --ipv6-host-prefix, round robin): a request waits
  for at most one request of each other host, however many requests they send;
- a client host has at most 2 requests waiting for the serial line or in
  progress on it, its next ones get exception 0x0A at once;
- --max-connections-per-host limits its number of TCP connections (default
  16), --max-connections the total (default 128): when it's reached, a new
  client takes the place of the most idle session (--no-evict-idle: refused);
  a session without any request for --idle-timeout (default 300 s) is closed.
With N hosts that send requests at the same time, each one gets 1/N of the
serial line: a client timeout must be long enough for N requests.
Refused requests are logged once, then once per 10 s at most (with their
count), so a request flood can't flood the log.

LIMITATIONS
-----------
- The response to a broadcast only means "queued for the bus": no slave
  confirms it, and a broadcast dropped by the gateway (too old) is not
  reported (a pyModbusTCP client can also send it with broadcast=True, that
  doesn't wait for this response).
- Throughput is limited by the serial line: requests are served one at a time.
- The content of an answer is only checked for the standard functions (0x01
  to 0x06, 0x0F, 0x10, 0x16, 0x17, 0x2B/0x0E). A reply later than the
  timeout, with exactly the shape of the answer of the next request to the
  same slave (same function and same quantity), can't be told apart from
  it: set -t above the response time of the slowest slave.

USAGE
-----
The default TCP port is 5020: port 502 (the standard Modbus/TCP port) is a
privileged port on Unix systems. Never run this command as root (or with sudo)
to listen on it: give the CAP_NET_BIND_SERVICE capability to the interpreter of
a dedicated virtual environment (see "Listen on the standard Modbus port 502
without root" in the pyModbusTCP examples README).
The user also needs access to the serial port (on Debian: group "dialout").

Open /dev/ttyUSB0 at 115200 bauds and relay Modbus/TCP requests (on localhost,
port 5020) to RTU slave(s):
$ mbtcp-tcp2rtu --baudrate 115200 /dev/ttyUSB0

Same, reachable from one network only, read-only, 8E1 serial format:
$ mbtcp-tcp2rtu -H 0.0.0.0 -A 192.168.1.0/24 --read-only \
    -b 19200 --parity E /dev/ttyUSB0

Relay the reads and the writes of registers only (no coil write):
$ mbtcp-tcp2rtu --functions 1,2,3,4,6,16 /dev/ttyUSB0

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
$ mbtcp-tcp2rtu --rs485 --rs485-rts-tx low --rs485-rts-rx high /dev/ttyRS485

Add -d (--debug) to log every RTU frame exchanged on the serial line, as a
hex dump (Tx = gateway to slave, Rx = slave to gateway), e.g.:
    DEBUG [mbtcp-tcp2rtu       ] RTU Tx [8 bytes] 01 03 00 00 00 01 84 0a
    DEBUG [mbtcp-tcp2rtu       ] RTU Rx [7 bytes] 01 03 02 12 34 b5 33

See --help for all options.
"""

import argparse
import logging
import queue
import sys
import time
from collections import deque
from threading import Condition, Event, Lock

from ._common import (
    DEFAULT_FUNCTIONS,
    PORT_502_HELP,
    READ_ONLY_FUNCTIONS,
    SERIAL_MISSING_MSG,
    FunctionFilter,
    add_ipv6_host_prefix_arg,
    add_rs485_args,
    add_version_arg,
    check_listen_args,
    check_rs485_args,
    function_filter,
    ip_or_network,
    is_permission_error,
    positive_float,
    positive_int,
    setup_rs485,
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

from ..constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    EXP_DATA_VALUE,
    EXP_GATEWAY_PATH_UNAVAILABLE,
    EXP_ILLEGAL_FUNCTION,
    GET_COMM_EVENT_COUNTER,
    GET_COMM_EVENT_LOG,
    MASK_WRITE_REGISTER,
    MEI_TYPE_READ_DEVICE_ID,
    READ_COILS,
    READ_DISCRETE_INPUTS,
    READ_EXCEPTION_STATUS,
    READ_FIFO_QUEUE,
    READ_FILE_RECORD,
    READ_HOLDING_REGISTERS,
    READ_INPUT_REGISTERS,
    REPORT_SERVER_ID,
    WRITE_FILE_RECORD,
    WRITE_MULTIPLE_COILS,
    WRITE_MULTIPLE_REGISTERS,
    WRITE_READ_MULTIPLE_REGISTERS,
    WRITE_SINGLE_COIL,
    WRITE_SINGLE_REGISTER,
)
from ..server import (
    GatewayPathUnavailable,
    GatewayTargetFailed,
    IllegalDataValue,
    MemoryStore,
    ModbusException,
    ModbusServer,
    Request,
    SlaveDeviceBusy,
)
from ..server._common import HOST_IPV6_PREFIX, LogThrottle, host_key
from ..server._validators import validate_ipv6_host_prefix
from . import _rtu
from ._rtu import (
    BROADCAST_ADDRESS,
    MAX_RTU_FRAME,
    MAX_SLAVE_ADDRESS,
    MIN_EOF_DELAY,
    SERIAL_IO_ERRORS,
    hexdump,
)

logger = logging.getLogger("mbtcp-tcp2rtu")

# requests waiting for the serial line, of all the client hosts, before the "gateway overloaded" exception is returned
QUEUE_SIZE = 16
# pending requests (waiting for the serial line or in progress) of a single client host: a host can't fill the queue
MAX_PENDING_PER_HOST = 2
# functions that can be broadcast (writes, no answer from the slaves) -> size of their normal response, the start of
# the request (None: the whole request, an echo)
BROADCAST_FUNCTIONS: dict[int, int | None] = {
    WRITE_SINGLE_COIL: 5, WRITE_SINGLE_REGISTER: 5, WRITE_MULTIPLE_COILS: 5, WRITE_MULTIPLE_REGISTERS: 5,
    WRITE_FILE_RECORD: None, MASK_WRITE_REGISTER: 7,
}
# default silent delay of the bus after a broadcast (s): the slaves execute it before the next frame
DEFAULT_BROADCAST_DELAY = 0.1
MAX_BROADCAST_DELAY = 10.0
# broadcasts that can wait for the bus (at most half of the queue: the other requests keep their place)
DEFAULT_BROADCAST_BUFFER = 4
MAX_BROADCAST_BUFFER = QUEUE_SIZE // 2
# a broadcast that waited longer for the bus is dropped (s): an old write is never applied long after its send
DEFAULT_BROADCAST_MAX_AGE = 10.0
MAX_BROADCAST_MAX_AGE = 60.0


def is_write_file_record(pdu: bytes) -> bool:
    """Check the format of a Write File Record (0x15) request PDU: a byte count of 0x09 to 0xFB that matches the
    data, then sub-requests of reference type 6, each with its record length and exactly its record data.

    A broadcast of this function is answered by the gateway with its echo, before the bus: a malformed one would be
    answered with an exception, but written on the bus anyway.
    """
    if len(pdu) < 2 or not 0x09 <= pdu[1] <= 0xFB or len(pdu) != 2 + pdu[1]:
        return False
    offset = 2
    while offset < len(pdu):
        # reference type (6), file number, record number, record length (in registers), then the record data
        if len(pdu) - offset < 7 or pdu[offset] != 0x06:
            return False
        record_length = int.from_bytes(pdu[offset + 5:offset + 7], 'big')
        offset += 7 + 2 * record_length
        if record_length == 0 or offset > len(pdu):
            return False
    return True


def is_read_file_record(pdu: bytes) -> bool:
    """Check the format of a Read File Record (0x14) request PDU: a byte count of 0x07 to 0xF5 that matches the
    data, made of sub-requests of 7 bytes, each of reference type 6."""
    if len(pdu) < 2 or not 0x07 <= pdu[1] <= 0xF5 or len(pdu) != 2 + pdu[1] or pdu[1] % 7:
        return False
    return all(pdu[offset] == 0x06 for offset in range(2, len(pdu), 7))


# the standard functions whose request PDU has a fixed size (function code included): the read and write functions,
# and the functions of the serial line without data (read exception status, comm event counter and log, report
# server id)
FIXED_SIZE_REQUESTS = {
    READ_COILS: 5, READ_DISCRETE_INPUTS: 5, READ_HOLDING_REGISTERS: 5, READ_INPUT_REGISTERS: 5,
    WRITE_SINGLE_COIL: 5, WRITE_SINGLE_REGISTER: 5, MASK_WRITE_REGISTER: 7,
    READ_EXCEPTION_STATUS: 1, GET_COMM_EVENT_COUNTER: 1, GET_COMM_EVENT_LOG: 1, REPORT_SERVER_ID: 1,
    READ_FIFO_QUEUE: 3,
}
# the standard functions whose request PDU ends with a byte count and its data -> the index of the byte count
BYTE_COUNT_REQUESTS = {WRITE_MULTIPLE_COILS: 5, WRITE_MULTIPLE_REGISTERS: 5, WRITE_READ_MULTIPLE_REGISTERS: 9}
# size of a read device identification request (0x2B/0x0E): MEI type, read device id code, object id
READ_DEVICE_ID_REQUEST_SIZE = 4


def request_size_ok(pdu: bytes) -> bool:
    """Check the size of a request PDU of a standard function: exactly its fields, no byte missing, no byte after them.

    The PDU is written on the serial line as is, in one RTU frame with a valid CRC at its end. A slave that finds the
    end of a frame by its size (from the function code and the byte count), instead of the silence of 3.5 chars, would
    take the bytes after the request for the next frame: a request to any slave, for any function, hidden behind an
    allowed one (like a write behind a read with --read-only, a diagnostics 0x08 behind a 0x03). Such a request never
    reaches the serial line.

    The functions whose size is unknown to the gateway (diagnostics 0x08, the other MEI types of 0x2B, the functions of
    a vendor...) are accepted as is: they are only relayed if --functions allows them.

    :param pdu: request PDU (function code first)
    :returns: True if the request can be written on the serial line
    """
    func_code = pdu[0]
    if func_code in FIXED_SIZE_REQUESTS:
        return len(pdu) == FIXED_SIZE_REQUESTS[func_code]
    if func_code in BYTE_COUNT_REQUESTS:
        index = BYTE_COUNT_REQUESTS[func_code]
        return len(pdu) > index and len(pdu) == index + 1 + pdu[index]
    if func_code == READ_FILE_RECORD:
        return is_read_file_record(pdu)
    if func_code == WRITE_FILE_RECORD:
        return is_write_file_record(pdu)
    if func_code == ENCAPSULATED_INTERFACE_TRANSPORT and pdu[1:2] == bytes([MEI_TYPE_READ_DEVICE_ID]):
        return len(pdu) == READ_DEVICE_ID_REQUEST_SIZE
    return True


def response_matches(req_pdu: bytes, resp_pdu: bytes) -> bool:
    """Check the content of a response PDU against its request PDU (same function code, already checked).

    RTU frames have no transaction id: a late reply of a slave (after the timeout of the previous request) can only
    be told apart from the expected one by its content. For the standard functions, the response has a size set by
    the request (functions 0x01 to 0x04, 0x17) or echoes a part of it (0x05, 0x06, 0x0F, 0x10, 0x16, 0x2B/0x0E).
    The other functions are accepted as is (their content is unknown to the gateway), and so is a request too short
    to be decoded (never written on the serial line by the gateway, see request_size_ok()).

    :param req_pdu: request PDU (function code first)
    :param resp_pdu: response PDU (function code first)
    :returns: True if the response may be the one of the request
    """
    func_code = req_pdu[0]
    if resp_pdu[0] & 0x80:
        # exception response: function code + exception code, nothing else
        return len(resp_pdu) == 2
    if func_code in (READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS,
                     WRITE_READ_MULTIPLE_REGISTERS):
        if len(req_pdu) < 5:
            return True
        quantity = int.from_bytes(req_pdu[3:5], 'big')
        if func_code in (READ_COILS, READ_DISCRETE_INPUTS):
            byte_count = (quantity + 7) // 8
        else:
            byte_count = 2 * quantity
        return len(resp_pdu) == 2 + byte_count and resp_pdu[1] == byte_count
    if func_code in (WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER):
        # echo of the request
        return len(req_pdu) < 5 or resp_pdu == req_pdu[:5]
    if func_code in (WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS):
        # address and quantity of the request
        return len(req_pdu) < 5 or resp_pdu == req_pdu[:5]
    if func_code == MASK_WRITE_REGISTER:
        return len(req_pdu) < 7 or resp_pdu == req_pdu[:7]
    if func_code == ENCAPSULATED_INTERFACE_TRANSPORT and req_pdu[1:2] == bytes([MEI_TYPE_READ_DEVICE_ID]):
        # MEI type and read device id code echoed, then conformity level, more follows, next id, number of objects
        return len(resp_pdu) >= 7 and resp_pdu[1:3] == req_pdu[1:3]
    return True


def find_response(raw: bytes, request: 'CheckedRTUFrame') -> bytes:
    """Return the response of request in the bytes received, empty if there is none.

    Usually raw is one frame. It may also be two frames read as one (a late reply of a slave followed too closely
    by the response): the cut is searched where a prefix has a valid CRC.
    """
    if CheckedRTUFrame(raw).is_response_to(request):
        return raw
    # incremental CRC of the prefixes: a cut is only tried after a prefix with a valid CRC (a frame end)
    crc = 0xFFFF
    for index, byte in enumerate(raw[:-4], start=1):
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        if crc == 0 and index >= 4:
            for part in (raw[:index], raw[index:]):
                if CheckedRTUFrame(part).is_response_to(request):
                    return part
    return b''


class CheckedRTUFrame(_rtu.ModbusRTUFrame):
    """A Modbus/RTU frame, that can be checked against a request."""

    def is_response_to(self, request: 'CheckedRTUFrame') -> bool:
        """Check that frame is a valid response matching the given request.

        Guards against late/stray frames from another slave or another
        function (the bus is shared and a slave may answer after our timeout):
        same slave address, same function code, and a content consistent with
        the request (see response_matches()).
        """
        return (self.is_valid
                # a response has at least one byte after the function code (data, or the code of an exception)
                and len(self.raw) > _rtu.MIN_RTU_FRAME
                and self.slave_address == request.slave_address
                # exception responses set the MSB of the function code
                and (self.function_code & 0x7F) == request.function_code
                and response_matches(request.pdu, self.pdu))


class RtuQuery:
    """Request container to deal with modbus serial worker."""

    def __init__(self, host: str = '') -> None:
        # client host of the request (see host_key()): the serial line is shared in turn between the hosts
        self.host = host
        self.completed = Event()
        # set when the client session stopped waiting: the worker skips the request (the line is not used for it)
        self.cancelled = False
        self.request = CheckedRTUFrame()
        self.response = CheckedRTUFrame()
        # time the request was queued (a broadcast that waited too long for the bus is dropped)
        self.queued_at = time.monotonic()

    @property
    def is_broadcast(self) -> bool:
        return self.request.slave_address == BROADCAST_ADDRESS


class FairQueue:
    """Queue of the requests waiting for the serial line, served in turn by client host (round robin).

    Each host has its own FIFO, and get() takes one request of each host in turn: a request waits for at most one
    request of each other host, however many requests they send. With a single FIFO, a few hosts could fill it and
    get every turn of the serial line. Thread-safe.
    """

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        # host -> its waiting requests (dict order: the order of the hosts for the next turns)
        self._queues: dict[str, deque[RtuQuery]] = {}
        self._size = 0
        self._cond = Condition()

    def qsize(self) -> int:
        with self._cond:
            return self._size

    def put(self, query: RtuQuery) -> None:
        """Add a request (never blocks).

        :raises queue.Full: if maxsize requests are already waiting
        """
        with self._cond:
            if self._size >= self.maxsize:
                raise queue.Full
            self._queues.setdefault(query.host, deque()).append(query)
            self._size += 1
            self._cond.notify()

    def get(self, timeout: float | None = None) -> RtuQuery:
        """Return the next request: the oldest one of the next host in turn.

        :param timeout: max time to wait for a request (None: wait forever, 0: don't wait)
        :raises queue.Empty: if there is no request (after timeout)
        """
        with self._cond:
            if not self._cond.wait_for(lambda: self._size > 0, timeout=timeout):
                raise queue.Empty
            # first host of the turn: take its oldest request, and put it at the end of the turn if it has others
            host = next(iter(self._queues))
            host_queue = self._queues.pop(host)
            query = host_queue.popleft()
            if host_queue:
                self._queues[host] = host_queue
            self._size -= 1
            return query

    def get_nowait(self) -> RtuQuery:
        return self.get(timeout=0)


class TcpToRtuWorker(_rtu.SerialLink):
    """A serial worker to manage I/O with RTU devices."""

    def __init__(self, port: Serial, timeout: float = 1.0, end_of_frame: float | None = None,
                 allow_broadcast: bool = False, read_only: bool = False, functions: FunctionFilter | None = None,
                 ipv6_host_prefix: int = HOST_IPV6_PREFIX,
                 broadcast_delay: float = DEFAULT_BROADCAST_DELAY,
                 broadcast_buffer: int = DEFAULT_BROADCAST_BUFFER,
                 broadcast_max_age: float = DEFAULT_BROADCAST_MAX_AGE) -> None:
        # public
        self.timeout = timeout
        # request filters (see is_allowed())
        self.allow_broadcast = allow_broadcast
        self.read_only = read_only
        # the relayed functions: read_only is a shortcut for READ_ONLY_FUNCTIONS (with the broadcast refused)
        if read_only and functions is not None:
            raise ValueError('read_only and functions are exclusive (read_only is a shortcut for a functions filter)')
        if functions is not None and not isinstance(functions, FunctionFilter):
            raise TypeError('functions must be a FunctionFilter (or None for the default one)')
        self.functions = READ_ONLY_FUNCTIONS if read_only else DEFAULT_FUNCTIONS if functions is None else functions
        # the client hosts that share the serial line in turn: IPv6 clients by network of this prefix length
        self.ipv6_host_prefix = validate_ipv6_host_prefix(ipv6_host_prefix)
        # silent delay of the bus after a broadcast
        if isinstance(broadcast_delay, bool) or not isinstance(broadcast_delay, (int, float)) \
                or not 0 <= broadcast_delay <= MAX_BROADCAST_DELAY:
            raise ValueError(f'broadcast delay must be between 0 and {MAX_BROADCAST_DELAY:g} s')
        self.broadcast_delay = float(broadcast_delay)
        # broadcasts that can wait for the bus, and the max time they wait
        if isinstance(broadcast_buffer, bool) or not isinstance(broadcast_buffer, int) \
                or not 1 <= broadcast_buffer <= MAX_BROADCAST_BUFFER:
            raise ValueError(f'broadcast buffer must be between 1 and {MAX_BROADCAST_BUFFER}')
        self.broadcast_buffer = broadcast_buffer
        if isinstance(broadcast_max_age, bool) or not isinstance(broadcast_max_age, (int, float)) \
                or not 0 < broadcast_max_age <= MAX_BROADCAST_MAX_AGE:
            raise ValueError(f'broadcast max age must be more than 0 and at most {MAX_BROADCAST_MAX_AGE:g} s')
        self.broadcast_max_age = float(broadcast_max_age)
        # the serial port, the interframe and end of frame delays
        super().__init__(port, end_of_frame, logger)
        # internal request queue: the hosts are served in turn (round robin), QUEUE_SIZE waiting requests at most
        # before the overloaded exception is returned
        self.rtu_queries_q = FairQueue(maxsize=QUEUE_SIZE)
        # pending requests of each client host (see host_key()) and its lock (session threads)
        self._host_pending: dict[str, int] = {}
        self._host_pending_lock = Lock()
        # refused requests are logged at most once per 10 s (a request flood must not flood the log)
        self._refused_log = LogThrottle(logger, level=logging.INFO)
        self._host_busy_log = LogThrottle(logger)
        self._overloaded_log = LogThrottle(logger)
        self._broadcast_busy_log = LogThrottle(logger, level=logging.INFO)
        self._broadcast_dropped_log = LogThrottle(logger)
        self._stray_log = LogThrottle(logger)
        self._error_log = LogThrottle(logger, level=logging.ERROR)
        # broadcasts in the queue (waiting for the bus), at most broadcast_buffer
        self._broadcast_lock = Lock()
        self._broadcasts_waiting = 0
        # worker stop flag
        self._stop = Event()
        self._last_bus_activity = 0.0

    def stop(self) -> None:
        """Ask the worker loop to exit."""
        self._stop.set()

    def _wait_bus_idle(self) -> None:
        """Wait for the end of the silent interval of the bus (between frames, or after a broadcast)."""
        # perf_counter(): the interframe delay is a few ms, monotonic() has a 15.6 ms step on Windows before
        # Python 3.13
        idle = self._last_bus_activity + self.interframe_delay - time.perf_counter()
        if idle > 0:
            time.sleep(idle)

    def _transaction(self, request: bytes) -> bytes:
        """Send a request frame on the serial line and return the raw answer.

        RTU frames have no transaction id: a frame that is not the response of this request (a late reply of a
        slave to a previous request, a corrupted frame) is ignored, and the gateway keeps listening for the right one
        until the timeout. A late reply with exactly the shape of the response (same function, same quantity) can't
        be told apart from it: the timeout must be longer than the response time of the slave.

        :return: raw frame of the response, empty if no valid answer before timeout
        :raises serialutil.SerialException: on serial I/O error
        """
        port = self.serial_port
        # respect the silent interval between frames
        self._wait_bus_idle()
        # send to serial
        port.reset_input_buffer()
        port.write(request)
        port.flush()
        logger.debug('RTU Tx %s', hexdump(request))
        if request[0] == BROADCAST_ADDRESS:
            # no slave answers a broadcast: no wait for an answer, the bus stays silent for broadcast_delay (the
            # interframe delay is added before the next frame)
            self._last_bus_activity = time.perf_counter() + self.broadcast_delay
            logger.debug('RTU broadcast, bus silent for %.3f s', self.broadcast_delay)
            return b''
        request_frame = CheckedRTUFrame(request)
        deadline = time.monotonic() + self.timeout
        while (remaining := deadline - time.monotonic()) > 0:
            rx_raw = self._read_frame(remaining)
            self._last_bus_activity = time.perf_counter()
            if not rx_raw:
                break
            logger.debug('RTU Rx %s', hexdump(rx_raw))
            response = find_response(rx_raw, request_frame)
            if response:
                return response
            self._stray_log.event('Frame on the serial line that is not the response of the request: %(count)d '
                                  'frame(s) ignored (a late reply of a slave? its timeout -t may be too short), '
                                  'last %(frame)s', frame=hexdump(rx_raw))
        logger.debug('RTU Rx timeout, no valid answer')
        return b''

    def _read_frame(self, timeout: float) -> bytes:
        """Wait at most timeout seconds for the first byte of a frame, then read it until the line is silent.

        :return: raw bytes received, empty if nothing before timeout
        """
        port = self.serial_port
        port.timeout = timeout
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

    def loop(self) -> None:
        """Serial worker main loop (returns after stop() is called)."""
        while not self._stop.is_set():
            # get next exchange from queue (periodically wake up to check the stop flag)
            try:
                rtu_query = self.rtu_queries_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if rtu_query.cancelled:
                # nobody waits for its answer anymore: don't hold the serial line for it
                rtu_query.completed.set()
                continue
            if rtu_query.is_broadcast and not self._broadcast_on_time(rtu_query):
                rtu_query.completed.set()
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
                self._error_log.event('Unexpected error in serial worker: %(count)d error(s)', exc_info=True)
                rtu_query.response.raw = b''
            finally:
                # mark all as done (always, or the TCP client thread would hang)
                rtu_query.completed.set()
        # on exit, release anyone still waiting
        self._drain_queue()

    def _broadcast_on_time(self, rtu_query: RtuQuery) -> bool:
        """A broadcast taken from the queue: True if it can still be written, False if it waited too long."""
        with self._broadcast_lock:
            self._broadcasts_waiting -= 1
        # the age on the bus, after its silent interval (it can be long after a previous broadcast)
        self._wait_bus_idle()
        age = time.monotonic() - rtu_query.queued_at
        if age <= self.broadcast_max_age:
            return True
        self._broadcast_dropped_log.event('Broadcast dropped, too old for the bus: %(count)d request(s) (last '
                                          'waited %(age).1f s, max %(max_age)g s)', age=age,
                                          max_age=self.broadcast_max_age)
        return False

    def _drain_queue(self) -> None:
        while True:
            try:
                rtu_query = self.rtu_queries_q.get_nowait()
            except queue.Empty:
                return
            rtu_query.completed.set()

    def is_allowed(self, unit_id: int, pdu: bytes) -> int:
        """Apply the gateway filters to a request.

        :param unit_id: unit id of the request (the RTU slave address)
        :param pdu: request PDU (at least the function code)
        :return: 0 if the request can be relayed, else the modbus exception code to return
        """
        # function codes are 1 to 127: a code with the exception flag (0x80 to 0xFF) or 0 is not a request, it is
        # never written on the bus (a slave may take it for the reply of another one, and it holds the line until
        # the timeout)
        func_code = pdu[0]
        if not 0x01 <= func_code <= 0x7F:
            return EXP_ILLEGAL_FUNCTION
        # broadcast: a write to every slave of the bus (never allowed in read-only mode), only for the functions
        # that write (no slave answers: a read has no meaning)
        if unit_id == BROADCAST_ADDRESS:
            if self.read_only or not self.allow_broadcast:
                return EXP_GATEWAY_PATH_UNAVAILABLE
            if func_code not in BROADCAST_FUNCTIONS:
                return EXP_ILLEGAL_FUNCTION
        # reserved addresses on a serial line
        if unit_id > MAX_SLAVE_ADDRESS:
            return EXP_GATEWAY_PATH_UNAVAILABLE
        # the relayed functions (--functions, --read-only): a whitelist, 0x2B by MEI type
        if not self.functions.allows(pdu):
            return EXP_ILLEGAL_FUNCTION
        # a standard function: exactly the size of its request, the bytes after it would be a hidden frame for a
        # slave that ends a frame by its size (see request_size_ok()), even with --functions all
        if not request_size_ok(pdu):
            return EXP_DATA_VALUE
        return 0

    def relay_request(self, req: Request) -> bytes:
        """Fallback handler of the Modbus server: every request is relayed to the serial line.

        :param req: the request of a client
        :returns: the response PDU of the RTU slave
        :raises ModbusException: a request refused by the gateway, or a slave that didn't answer
        """
        # gateway filters: refused requests never reach the serial line
        unit_id = req.ctx.unit_id
        client = req.ctx.client
        exp_code = self.is_allowed(unit_id, req.pdu)
        if exp_code:
            self._refused_log.event('Request refused by the gateway filters: %(count)d request(s) (last: unit '
                                    '%(unit_id)d, function 0x%(func_code)02X, exception 0x%(exp_code)02X, from '
                                    '%(client)s)', unit_id=unit_id, func_code=req.pdu[0], exp_code=exp_code,
                                    client=client.address)
            raise ModbusException(code=exp_code)
        # fair share of the serial line: a client host can't fill the queue
        host = host_key((client.address, client.port), self.ipv6_host_prefix)
        if not self._acquire_host_slot(host):
            self._host_busy_log.event('Too many pending requests from a host: %(count)d request(s) rejected '
                                      '(limit %(limit)d, last from %(host)s)', limit=MAX_PENDING_PER_HOST, host=host)
            raise GatewayPathUnavailable()
        try:
            return self._relay(req, host)
        finally:
            self._release_host_slot(host)

    def _acquire_host_slot(self, host: str) -> bool:
        """Count a new pending request of a client host, False if it already has MAX_PENDING_PER_HOST."""
        with self._host_pending_lock:
            pending = self._host_pending.get(host, 0)
            if pending >= MAX_PENDING_PER_HOST:
                return False
            self._host_pending[host] = pending + 1
            return True

    def _release_host_slot(self, host: str) -> None:
        with self._host_pending_lock:
            pending = self._host_pending.get(host, 0) - 1
            if pending > 0:
                self._host_pending[host] = pending
            else:
                self._host_pending.pop(host, None)

    def _relay(self, req: Request, host: str = '') -> bytes:
        """Relay the request to the serial line.

        :param host: client host of the request (see host_key()), the serial line is shared in turn between hosts
        :returns: the response PDU of the slave
        :raises ModbusException: gateway overloaded or serial worker not responding (0x0A), no valid answer (0x0B),
            broadcast while the broadcast buffer is full (0x06)
        """
        if req.ctx.unit_id == BROADCAST_ADDRESS:
            return self._relay_broadcast(req, host)
        rtu_query = self._run_query(req, host)
        # check receive frame status
        if rtu_query.response.is_response_to(rtu_query.request):
            return rtu_query.response.pdu
        # slave failed to respond
        logger.debug('No valid answer from unit %d', rtu_query.request.slave_address)
        raise GatewayTargetFailed()

    def _relay_broadcast(self, req: Request, host: str) -> bytes:
        """Relay a broadcast request: queued for the bus, answered at once with the normal response of its function.

        No slave answers a broadcast, so the client gets the response as soon as the request is in the queue. A few
        broadcasts can wait for the bus (broadcast_buffer): beyond, the next one gets exception 0x06 (slave device busy)
        at once. A broadcast that waited more than broadcast_max_age is dropped by the worker (see loop()): a client
        that sends broadcasts faster than the bus can take them never builds a backlog of old writes.
        """
        # the normal response of the function: the start of the request (checked before the bus: once queued, the
        # broadcast is written, the client must never get an exception for it)
        size = BROADCAST_FUNCTIONS[req.pdu[0]]
        if size is not None and len(req.pdu) < size:
            raise IllegalDataValue()
        # the echo of a whole request (0x15): its format is checked, a malformed one (like the function code alone)
        # gave a response refused by the server ("slave device failure"), while the frame was queued for the bus
        if size is None and not is_write_file_record(req.pdu):
            raise IllegalDataValue()
        response = req.pdu if size is None else req.pdu[:size]
        with self._broadcast_lock:
            if self._broadcasts_waiting >= self.broadcast_buffer:
                self._broadcast_busy_log.event('Broadcast refused, the broadcast buffer is full: %(count)d '
                                               'request(s) (%(waiting)d waiting, last from %(client)s)',
                                               waiting=self.broadcast_buffer, client=req.ctx.client.address)
                raise SlaveDeviceBusy()
            self._broadcasts_waiting += 1
        try:
            self._queue(req, host)
        except BaseException:
            with self._broadcast_lock:
                self._broadcasts_waiting -= 1
            raise
        return response

    def _queue(self, req: Request, host: str) -> RtuQuery:
        """Put the request in the queue of the serial line.

        :raises ModbusException: gateway overloaded (0x0A)
        """
        # init a serial exchange from the request
        rtu_query = RtuQuery(host)
        rtu_query.request.build(raw_pdu=req.pdu, slave_address=req.ctx.unit_id)
        try:
            # add a request in the serial worker queue, can raise queue.Full
            self.rtu_queries_q.put(rtu_query)
        except queue.Full:
            # overloaded gateway
            self._overloaded_log.event('Gateway overloaded: %(count)d request(s) rejected (queue full, last from '
                                       '%(client)s)', client=req.ctx.client.address)
            raise GatewayPathUnavailable() from None
        return rtu_query

    def _run_query(self, req: Request, host: str) -> RtuQuery:
        """Queue the request for the serial line and wait for the end of its exchange.

        :raises ModbusException: gateway overloaded or serial worker not responding (0x0A)
        """
        rtu_query = self._queue(req, host)
        # wait result (bounded: worst case every queued request times out)
        wait_max = (QUEUE_SIZE + 1) * (self.timeout + self.end_of_frame) + 1.0
        if not rtu_query.completed.wait(timeout=wait_max):
            # the worker must not use the serial line for a request that nobody waits for
            rtu_query.cancelled = True
            logger.error('Serial worker not responding (unit %d)', rtu_query.request.slave_address)
            raise GatewayPathUnavailable()
        return rtu_query


def broadcast_delay(value: str) -> float:
    """argparse type: the silent delay of the bus after a broadcast (0 to MAX_BROADCAST_DELAY s)."""
    try:
        delay = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not a number') from None
    if not 0 <= delay <= MAX_BROADCAST_DELAY:
        raise argparse.ArgumentTypeError(f'{value!r} out of 0 to {MAX_BROADCAST_DELAY:g} s')
    return delay


def broadcast_buffer(value: str) -> int:
    """argparse type: the broadcasts that can wait for the bus (1 to MAX_BROADCAST_BUFFER)."""
    try:
        size = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an integer') from None
    if not 1 <= size <= MAX_BROADCAST_BUFFER:
        raise argparse.ArgumentTypeError(f'{value!r} out of 1 to {MAX_BROADCAST_BUFFER}')
    return size


def broadcast_max_age(value: str) -> float:
    """argparse type: the max time a broadcast waits for the bus (0 excluded to MAX_BROADCAST_MAX_AGE s)."""
    try:
        age = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not a number') from None
    if not 0 < age <= MAX_BROADCAST_MAX_AGE:
        raise argparse.ArgumentTypeError(f'{value!r} out of 0 (excluded) to {MAX_BROADCAST_MAX_AGE:g} s')
    return age


def log_access_settings(srv: ModbusServer, worker: TcpToRtuWorker) -> None:
    """Log the access control of the gateway at startup: the allowed clients and the relayed functions."""
    if srv.allowed_hosts is not None:
        logger.info('Allowed clients: %s', ', '.join(srv.allowed_hosts) or 'none')
    if worker.read_only:
        logger.info('Read-only mode: only read functions are relayed')
    logger.info('Relayed functions: %s', worker.functions.text())
    if worker.functions.codes is None:
        logger.warning('Every function is relayed (--functions all): diagnostics, file records and vendor '
                       'functions can restart, silence or reprogram the slaves')


def main(argv: list[str] | None = None) -> int:
    # parse args
    parser = argparse.ArgumentParser(prog='mbtcp-tcp2rtu', description='Modbus/TCP to Modbus/RTU gateway',
                                     epilog='Port 502 without root: ' + PORT_502_HELP)
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-H', '--host', type=str, default='localhost',
                        help='listen address (default: localhost), 0.0.0.0 for every IPv4 interface')
    parser.add_argument('-p', '--port', type=tcp_port, default=5020, help='TCP port (default: 5020)')
    parser.add_argument('-6', '--ipv6', action='store_true', help='use IPv6 (like -6 -H ::1)')
    parser.add_argument('-A', '--allow', action='append', type=ip_or_network, metavar='IP_OR_NETWORK',
                        help='allowed client IP address or network (like 10.0.0.0/24), repeatable '
                             '(default: every client)')
    functions = parser.add_mutually_exclusive_group()
    functions.add_argument('--functions', type=function_filter, default=None, metavar='CODES',
                           help='relayed functions, separated by commas, like "1,2,3,4,6,16,0x2B/0x0E" (0x2B with '
                                'its MEI type, 0x0E if not given), or "all" (default: '
                                f'{DEFAULT_FUNCTIONS.text()}); other functions get exception 0x01')
    functions.add_argument('--read-only', action='store_true',
                           help=f'only relay read functions ({READ_ONLY_FUNCTIONS.text()}), refuse broadcasts: a '
                                'shortcut for --functions')
    parser.add_argument('--max-connections', type=positive_int, default=128,
                        help='max number of simultaneous clients (default: 128), when reached a new client takes '
                             'the place of the most idle one')
    parser.add_argument('--no-evict-idle', dest='evict_idle', action='store_false',
                        help='refuse the new clients when --max-connections is reached (default: close the most '
                             'idle session for them)')
    parser.add_argument('--max-connections-per-host', type=positive_int, default=16,
                        help='max number of simultaneous connections of a client host (default: 16)')
    add_ipv6_host_prefix_arg(parser)
    parser.add_argument('--idle-timeout', type=positive_float, default=300.0,
                        help='close a client session without request after this delay in s (default: 300)')
    parser.add_argument('--allow-broadcast', action='store_true',
                        help='relay broadcast requests (unit id 0, write functions only) to every slave, answered '
                             'at once')
    parser.add_argument('--broadcast-delay', type=broadcast_delay, default=DEFAULT_BROADCAST_DELAY, metavar='S',
                        help='silent delay of the bus after a broadcast, 0 to '
                             f'{MAX_BROADCAST_DELAY:g} s (default: {DEFAULT_BROADCAST_DELAY:g})')
    parser.add_argument('--broadcast-buffer', type=broadcast_buffer, default=DEFAULT_BROADCAST_BUFFER, metavar='N',
                        help='broadcasts that can wait for the bus, 1 to '
                             f'{MAX_BROADCAST_BUFFER} (default: {DEFAULT_BROADCAST_BUFFER})')
    parser.add_argument('--broadcast-max-age', type=broadcast_max_age, default=DEFAULT_BROADCAST_MAX_AGE,
                        metavar='S', help='a broadcast that waited longer for the bus is dropped, up to '
                                          f'{MAX_BROADCAST_MAX_AGE:g} s (default: {DEFAULT_BROADCAST_MAX_AGE:g})')
    parser.add_argument('-b', '--baudrate', type=positive_int, default=9600, help='serial rate (default: 9600)')
    parser.add_argument('--bytesize', type=int, choices=(7, 8), default=8, help='data bits (default: 8)')
    parser.add_argument('--parity', choices=('N', 'E', 'O'), default='N', help='parity (default: N)')
    parser.add_argument('--stopbits', type=float, choices=(1, 1.5, 2), default=1, help='stop bits (default: 1)')
    parser.add_argument('-t', '--timeout', type=positive_float, default=1.0, help='serial timeout in s (default: 1.0)')
    parser.add_argument('-e', '--eof', type=positive_float, default=None,
                        help='serial end of frame delay in s (default: computed from baudrate, '
                             f'min {MIN_EOF_DELAY:.3f} s)')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode')
    add_rs485_args(parser)
    add_version_arg(parser)
    args = parser.parse_args(argv)
    check_listen_args(parser, args)
    check_rs485_args(parser, args)

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
        rs485 = setup_rs485(serial_port, args)
        if rs485 is not None:
            logger.info('RS-485 mode: %s', rs485.text())

        # init serial worker
        serial_worker = TcpToRtuWorker(serial_port, args.timeout, args.eof,
                                       allow_broadcast=args.allow_broadcast, read_only=args.read_only,
                                       functions=args.functions, ipv6_host_prefix=args.ipv6_host_prefix,
                                       broadcast_delay=args.broadcast_delay,
                                       broadcast_buffer=args.broadcast_buffer,
                                       broadcast_max_age=args.broadcast_max_age)
        logger.debug('Interframe delay %.4f s, end of frame delay %.4f s',
                     serial_worker.interframe_delay, serial_worker.end_of_frame)

        # start modbus server: no local function, every request goes to the fallback that relays it
        logger.info('Start modbus server (%s, %d)', args.host, args.port)
        # note: the serial worker applies the function filter and the read-only mode itself (broadcast, MEI types...)
        srv = ModbusServer(host=args.host, port=args.port, no_block=True, ipv6=args.ipv6, store=MemoryStore(size=0),
                           functions={}, idle_timeout=args.idle_timeout,
                           fallback=serial_worker.relay_request,
                           allowed_hosts=args.allow, max_connections=args.max_connections,
                           max_connections_per_host=args.max_connections_per_host,
                           evict_idle=args.evict_idle,
                           ipv6_host_prefix=args.ipv6_host_prefix)
        log_access_settings(srv, serial_worker)
        srv.start()

        # start serial worker loop (until Ctrl-C or SIGTERM)
        logger.info('Start serial worker')
        with stop_on_sigterm(serial_worker.stop, logger):
            serial_worker.loop()
        return 0

    except serialutil.SerialException as e:
        logger.critical('Serial device error: %r', e)
        return 1
    except ModbusServer.Error as e:
        logger.critical('Modbus server error: %r', e)
        if is_permission_error(e) and args.port < 1024:
            logger.critical('Port %d is privileged: %s', args.port, PORT_502_HELP)
        return 2
    except ValueError as e:
        # invalid parameter value (like a baudrate or a RS-485 mode refused by the serial port)
        logger.critical('Invalid parameter: %s', e)
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
