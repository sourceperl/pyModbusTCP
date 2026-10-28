"""
mbtcp-rtu-scan: find the serial settings and the address of Modbus/RTU slaves
=============================================================================

Run it as "mbtcp-rtu-scan" (pyModbusTCP-cli package) or "python -m pyModbusTCP.tools rtu-scan".
It needs pyserial: pip install "pyModbusTCP[serial]" (installed with pyModbusTCP-cli).

WHAT IT DOES
------------
For a slave whose settings are unknown: the tool is a Modbus/RTU master that
sends a read request (holding register 0 by default, -F for another read
function) with each baud rate and serial format, from the most common to the
least common, to each slave address in ascending order (1 to 247). Any valid
answer, an exception included (a slave without holding register 0 answers
"illegal data address", one without the function "illegal function"), gives
the settings of the slave, printed with the mbtcp-tcp2rtu command that uses
them. The slaves of a bus share the serial settings: --all then reads every
address with these settings, to list the other slaves.

The default order: baud rates 9600, 19200, 38400, 115200, 57600, 4800, 2400,
1200, 14400, 28800, 76800, 230400; serial formats 8N1 (the most common in the
field), 8E1 (the default of the Modbus serial line spec), 8O1, 8N2. Each
baud rate and format with all the addresses takes about 2.5 minutes with the
default timeout (0.6 s), every setting about 2 hours: give the settings or the
addresses you know (-b, --parity, --stopbits, -a), or a shorter -t for a fast
slave. A setting refused by the serial port (a baud rate the adapter doesn't
support) is skipped.

An answer with a bad CRC or from another address is a hint (something answers,
with other settings, or a noisy line): listed at the end if no slave is found.

The tool only sends read requests, but it is a master: stop the usual master
of the bus (PLC, SCADA, gateway) while it runs, two masters collide.

EXAMPLES
--------
Find a slave on /dev/ttyUSB0 (COM3 on Windows), every setting:
$ mbtcp-rtu-scan /dev/ttyUSB0

At 9600 or 19200 bauds, even parity only, slaves 1 to 10, then the other
slaves of the bus:
$ mbtcp-rtu-scan -b 9600,19200 --parity E -a 1-10 --all /dev/ttyUSB0

A slave with input registers only:
$ mbtcp-rtu-scan -f 4 /dev/ttyUSB0

Add -d (--debug) to log every RTU frame (Tx = request, Rx = answer). The
RS-485 options are the ones of mbtcp-tcp2rtu (see its documentation).

Exit status: 0 a slave is found, 1 no slave found or serial error, 2 invalid
parameter (or a RS-485 mode refused by the serial driver), 130 interrupted.
"""

import argparse
import logging
import struct
import sys
from dataclasses import dataclass, field

from ._common import (
    MAX_SLAVE_ADDRESS,
    SERIAL_MISSING_MSG,
    add_rs485_args,
    add_version_arg,
    check_rs485_args,
    positive_float,
    positive_int,
    setup_logging,
    setup_rs485,
    slave_address,
)

try:
    from serial import PARITY_EVEN, PARITY_NONE, PARITY_ODD, Serial
except ModuleNotFoundError as _exc:
    # pyserial is optional: give the way to install it instead of a bare "No module named 'serial'"
    if _exc.name != 'serial':
        raise
    raise ModuleNotFoundError(SERIAL_MISSING_MSG, name='serial') from _exc

from ._rtu import (
    MAX_RTU_FRAME,
    MIN_EOF_DELAY,
    SERIAL_IO_ERRORS,
    ModbusRTUFrame,
    hexdump,
    t35,
)

logger = logging.getLogger('mbtcp-rtu-scan')

# from the most common to the least common
BAUDRATES = (9600, 19200, 38400, 115200, 57600, 4800, 2400, 1200, 14400, 28800, 76800, 230400)
PARITIES = ('N', 'E', 'O')
# 2 stop bits: tried with no parity only (8N2, the format of the Modbus spec without parity), unless --stopbits is given
STOPBITS = (1, 2)
PYSERIAL_PARITY = {'N': PARITY_NONE, 'E': PARITY_EVEN, 'O': PARITY_ODD}
# the read functions (1 item at address 0): code -> name of the item
FUNCTIONS = {1: 'coil', 2: 'discrete input', 3: 'holding register', 4: 'input register'}
DEFAULT_FUNCTION = 3
DEFAULT_TIMEOUT = 0.6
EXCEPTIONS = {1: 'illegal function', 2: 'illegal data address', 3: 'illegal data value', 4: 'slave device failure',
              6: 'slave device busy'}
# the longest answer of the requests (1 register): address, function, byte count, 2 bytes, CRC
MAX_ANSWER = 7
# max number of hints printed when no slave is found
MAX_HINTS = 20
# width of the progress line rewritten in place on a terminal
LINE_WIDTH = 60
# a baud rate or a format refused by the serial port (pyserial raises ValueError for some of them)
REFUSED_ERRORS: tuple[type[BaseException], ...] = (ValueError, *SERIAL_IO_ERRORS)


# --- command line types ---

def address_list(value: str) -> list[int]:
    """argparse type: slave addresses like "1-10,247" (1 to 247, ranges, any notation), in the given order."""
    addresses: list[int] = []
    for part in value.split(','):
        first, sep, last = part.strip().partition('-')
        start = slave_address(first)
        end = slave_address(last) if sep else start
        if end < start:
            raise argparse.ArgumentTypeError(f'empty range {part.strip()!r}')
        addresses.extend(range(start, end + 1))
    return addresses


def baudrate_list(value: str) -> list[int]:
    """argparse type: baud rates like "9600,19200", in the given order."""
    return [positive_int(part.strip()) for part in value.split(',')]


def parity_list(value: str) -> list[str]:
    """argparse type: parities like "E,N" (N, E or O), in the given order."""
    parities = [part.strip().upper() for part in value.split(',')]
    for parity in parities:
        if parity not in PARITIES:
            raise argparse.ArgumentTypeError(f'invalid parity {parity!r} (choose from N, E, O)')
    return parities


def stopbits_list(value: str) -> list[int]:
    """argparse type: stop bits like "1,2", in the given order."""
    stopbits = [part.strip() for part in value.split(',')]
    for bits in stopbits:
        if bits not in ('1', '2'):
            raise argparse.ArgumentTypeError(f'invalid stop bits {bits!r} (choose from 1, 2)')
    return [int(bits) for bits in stopbits]


def ranges(values: list[int]) -> str:
    """[2, 3, ..., 10, 247] -> "2-10,247" (the reverse of address_list())."""
    parts: list[str] = []
    start = previous = values[0]
    for value in [*values[1:], None]:
        if value is not None and value == previous + 1:
            previous = value
            continue
        parts.append(str(start) if start == previous else f'{start}-{previous}')
        if value is not None:
            start = previous = value
    return ','.join(parts)


# --- scan ---

@dataclass
class SerialFormat:
    """A baud rate and a serial format (8 data bits: RTU mode)."""
    baudrate: int
    parity: str
    stopbits: int

    def __str__(self) -> str:
        return f'{self.baudrate} 8{self.parity}{self.stopbits}'


def serial_formats(args: argparse.Namespace) -> list[SerialFormat]:
    """The baud rates and formats to try, in this order: for each baud rate, each stop bits, each parity.

    2 stop bits are only tried with no parity (8N2), unless --stopbits is given.
    """
    stopbits = args.stopbits if args.stopbits is not None else STOPBITS
    return [SerialFormat(baudrate, parity, bits) for baudrate in args.baudrate for bits in stopbits
            for parity in args.parity if args.stopbits is not None or bits == 1 or parity == 'N']


@dataclass
class Answer:
    """The answer of a slave to a probe: valid (the slave answers with these settings) or a hint."""
    address: int
    frame: bytes
    valid: bool

    def describe(self) -> str:
        """Like "holding register 0 = 1234" or "exception 0x02 (illegal data address)"."""
        if self.frame[1] & 0x80:
            code = self.frame[2]
            return f'exception {code:#04x} ({EXCEPTIONS.get(code, "other")})'
        function = self.frame[1]
        # a bit (coil, discrete input): the low bit of the first data byte; a register: 2 bytes, big endian
        value = self.frame[3] & 1 if function in (1, 2) else struct.unpack('>H', self.frame[3:5])[0]
        return f'{FUNCTIONS[function]} 0 = {value}'


@dataclass
class Scanner:
    """A Modbus/RTU master that probes the slaves of a serial port.

    :param port: the serial port (open)
    :param function: the read function of the probes (1 to 4)
    :param timeout: the response time of a slave (s)
    :param eof: the silence that ends a frame (s), None for the 3.5 chars of the baud rate (at least MIN_EOF_DELAY)
    :param live: rewrite the address in test in place on the terminal
    """
    port: Serial
    function: int = DEFAULT_FUNCTION
    timeout: float = DEFAULT_TIMEOUT
    eof: float | None = None
    live: bool = False
    hints: list[str] = field(default_factory=list)

    def set_format(self, fmt: SerialFormat) -> bool:
        """Set a baud rate and format on the port, False if the port refuses it (it is then skipped)."""
        try:
            self.port.baudrate = fmt.baudrate
            self.port.parity = PYSERIAL_PARITY[fmt.parity]
            self.port.stopbits = fmt.stopbits
        except REFUSED_ERRORS as e:
            self.show_line(f'{fmt}: refused by the serial port, skipped ({e})')
            return False
        return True

    def read_frame(self, first_byte_timeout: float, end_of_frame: float) -> bytes:
        """Wait for the first byte, then read up to a silence (the end of the frame), MAX_RTU_FRAME bytes at most.

        A line that never goes silent (another master, a wrong baud rate on a busy bus) can't hold the scan: the read
        stops at the size of the largest frame (the next probe flushes the rest).
        """
        self.port.timeout = first_byte_timeout
        frame = bytearray(self.port.read(1))
        self.port.timeout = end_of_frame
        while frame and len(frame) < MAX_RTU_FRAME:
            chunk = self.port.read(min(max(self.port.in_waiting, 1), MAX_RTU_FRAME - len(frame)))
            if not chunk:
                break
            frame += chunk
        return bytes(frame)

    def probe(self, address: int) -> Answer | None:
        """Send the read request to address: its answer (valid or not), None if nothing is received."""
        port = self.port
        request = ModbusRTUFrame()
        request.build(struct.pack('>BHH', self.function, 0, 1), address)
        char_time = t35(port.baudrate, port.bytesize, port.parity, port.stopbits) / 3.5
        end_of_frame = self.eof if self.eof is not None else max(3.5 * char_time, MIN_EOF_DELAY)
        # the response time of the slave, plus the time to send the request and the answer
        first_byte_timeout = self.timeout + (len(request.raw) + MAX_ANSWER) * char_time
        port.reset_input_buffer()
        logger.debug('RTU Tx %s', request)
        port.write(request.raw)
        port.flush()
        frame = self.read_frame(first_byte_timeout, end_of_frame)
        # an adapter with a local echo (some RS-485 adapters) returns the request first
        if frame.startswith(request.raw):
            frame = frame[len(request.raw):] or self.read_frame(first_byte_timeout, end_of_frame)
        if not frame:
            return None
        logger.debug('RTU Rx %s', hexdump(frame))
        return Answer(address, frame, self.is_answer(ModbusRTUFrame(frame), address))

    def is_answer(self, frame: ModbusRTUFrame, address: int) -> bool:
        """True if frame is the answer of address to the request: a response or an exception, of the right size."""
        if not frame.is_valid or frame.slave_address != address:
            return False
        if frame.function_code == self.function:
            return len(frame.raw) == 5 + frame.raw[2]
        return frame.function_code == self.function | 0x80 and len(frame.raw) == 5

    def show_progress(self, text: str) -> None:
        """On a terminal, rewrite the current line with text."""
        if self.live:
            print('\r' + text.ljust(LINE_WIDTH), end='', flush=True)

    def show_line(self, text: str) -> None:
        """Print text on its own line, over the progress line on a terminal."""
        print(('\r' + text.ljust(LINE_WIDTH)) if self.live else text, flush=True)

    def find(self, formats: list[SerialFormat], addresses: list[int]) -> Answer | None:
        """Try every address with every format: the answer of the first slave found (the port keeps its format)."""
        label = ('address ' if len(addresses) == 1 else 'addresses ') + ranges(addresses)
        for fmt in formats:
            if not self.set_format(fmt):
                continue
            if not self.live:
                print(f'test: {fmt}, {label}', flush=True)
            for address in addresses:
                self.show_progress(f'test: {fmt}, address {address} ({ranges(addresses)})')
                answer = self.probe(address)
                if answer is not None and answer.valid:
                    if self.live:
                        print()
                    return answer
                if answer is not None:
                    self.hints.append(f'{fmt}, address {address}: {hexdump(answer.frame)}')
            if self.live:
                self.show_line(f'test: {fmt}, {label}: no answer')
        return None

    def scan_bus(self, found: int) -> list[Answer]:
        """Every other address with the format of the port: the other slaves of the bus."""
        answers = []
        for address in range(1, MAX_SLAVE_ADDRESS + 1):
            if address == found:
                continue
            self.show_progress(f'test: address {address} (1-{MAX_SLAVE_ADDRESS})')
            answer = self.probe(address)
            if answer is not None and answer.valid:
                self.show_line(f'slave found at address {address}, answer: {answer.describe()}')
                answers.append(answer)
        self.show_progress('')
        if self.live:
            print('\r', end='')
        return answers


# --- command line ---

def tcp2rtu_command(args: argparse.Namespace, fmt: SerialFormat) -> str:
    """The mbtcp-tcp2rtu command for the settings found (with the RS-485 options given)."""
    options = [f'-b {fmt.baudrate}', f'--parity {fmt.parity}', f'--stopbits {fmt.stopbits}']
    if args.rs485:
        options.append('--rs485')
        for name in ('rs485_rts_tx', 'rs485_rts_rx', 'rs485_delay_before_tx', 'rs485_delay_before_rx'):
            if getattr(args, name) is not None:
                options.append(f'--{name.replace("_", "-")} {getattr(args, name)}')
    return f'mbtcp-tcp2rtu {" ".join(options)} {args.device}'


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='mbtcp-rtu-scan', description='Find the serial settings and the address of Modbus/RTU slaves',
        epilog='Stop the usual master of the bus while it runs. Exit status: 0 a slave is found, 1 no slave found '
               'or serial error, 2 invalid parameter, 130 interrupted (Ctrl-C).')
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-b', '--baudrate', type=baudrate_list, default=list(BAUDRATES), metavar='RATES',
                        help='serial rates like 9600,19200, in the order to try (default: '
                             + ', '.join(map(str, BAUDRATES)) + ')')
    parser.add_argument('--parity', type=parity_list, default=list(PARITIES), metavar='PARITIES',
                        help='parities (N, E, O), in the order to try (default: N,E,O)')
    parser.add_argument('--stopbits', type=stopbits_list, default=None, metavar='BITS',
                        help='stop bits (1, 2), in the order to try (default: 1, then 2 with parity N only)')
    parser.add_argument('-a', '--address', type=address_list, default=list(range(1, MAX_SLAVE_ADDRESS + 1)),
                        metavar='ADDRESSES',
                        help=f'slave addresses, in the order to try, like 1-10,247 (default: 1-{MAX_SLAVE_ADDRESS})')
    functions = ', '.join(f'{code} {name}s' for code, name in FUNCTIONS.items())
    parser.add_argument('-F', '--function', type=int, choices=list(FUNCTIONS), default=DEFAULT_FUNCTION,
                        help=f'read function of the requests: {functions} (default: {DEFAULT_FUNCTION})')
    parser.add_argument('-t', '--timeout', type=positive_float, default=DEFAULT_TIMEOUT,
                        help=f'response time of a slave in s (default: {DEFAULT_TIMEOUT})')
    parser.add_argument('-e', '--eof', type=positive_float, default=None,
                        help='serial end of frame delay in s (default: computed from baudrate, '
                             f'min {MIN_EOF_DELAY:.3f} s)')
    parser.add_argument('--all', action='store_true',
                        help='then read every address with the settings found: the other slaves of the bus')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode: log every RTU frame')
    add_rs485_args(parser)
    add_version_arg(parser)
    args = parser.parse_args(argv)
    check_rs485_args(parser, args)
    return args


def print_hints(hints: list[str]) -> None:
    if hints:
        print('answers with a bad CRC or from another address (other settings, or a noisy line):')
        print('\n'.join(hints[:MAX_HINTS]))
        if len(hints) > MAX_HINTS:
            print(f'... and {len(hints) - MAX_HINTS} more')


def scan(port: Serial, args: argparse.Namespace) -> int:
    """Find a slave, then the other slaves of the bus with --all: the exit status."""
    # the address in test on the line of its format: on a terminal only, and not with the frames of the debug log
    scanner = Scanner(port, args.function, args.timeout, args.eof, live=sys.stdout.isatty() and not args.debug)
    formats = serial_formats(args)
    name = FUNCTIONS[args.function]
    print(f'probe request: read {name}s (function {args.function:#04x}) of {name} 0, response timeout '
          f'{args.timeout:g} s', flush=True)
    answer = scanner.find(formats, args.address)
    if answer is None:
        print('\nno slave found')
        print_hints(scanner.hints)
        return 1
    fmt = SerialFormat(port.baudrate, {v: k for k, v in PYSERIAL_PARITY.items()}[port.parity], int(port.stopbits))
    print(f'\nslave found at address {answer.address} ({fmt}), answer: {answer.describe()}')
    print('\nto use these settings with the Modbus/TCP to Modbus/RTU gateway:')
    print('  ' + tcp2rtu_command(args, fmt))
    if args.all:
        print(f'\nother slaves at {fmt}:')
        if not scanner.scan_bus(answer.address):
            print('none')
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # the frames are only logged in debug mode
    setup_logging(args.debug)
    port = None
    try:
        port = Serial(port=args.device, bytesize=8)
        rs485 = setup_rs485(port, args)
        if rs485 is not None:
            logger.info('RS-485 mode: %s', rs485.text())
        return scan(port, args)
    except ValueError as e:
        # a RS-485 mode refused by the serial driver
        logger.critical('Invalid parameter: %s', e)
        return 2
    except SERIAL_IO_ERRORS as e:
        print(f'\nserial device error: {e!r}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\ninterrupted', file=sys.stderr)
        return 130
    finally:
        if port is not None and port.is_open:
            try:
                port.close()
            except SERIAL_IO_ERRORS as e:
                logger.debug('Error while closing serial port: %r', e)


if __name__ == '__main__':
    sys.exit(main())
