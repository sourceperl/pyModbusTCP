"""
mbtcp-client: a Modbus/TCP client for the command line
======================================================

Run it as "mbtcp-client" (pyModbusTCP-cli package) or "python -m pyModbusTCP.tools client".

Send one Modbus/TCP request (read or write) and print the result.

USAGE
-----
$ mbtcp-client -H 192.168.1.10 read-holding 0 -n 4         # 4 holding registers from address 0
$ mbtcp-client -H 192.168.1.10 read-holding 0 -i 1.0       # read again every second (Ctrl-C to stop)
$ mbtcp-client -H 192.168.1.10 read-coils 100 -n 8
$ mbtcp-client -H 192.168.1.10 read-holding 0 -n 4 -f plain --hex   # "address value" lines, for a script
$ mbtcp-client -H 192.168.1.10 read-holding 0 -n 4 -f json          # one JSON object per read
$ mbtcp-client -H 192.168.1.10 write-register 10 0x1234
$ mbtcp-client -H 192.168.1.10 write-registers 10 1 2 3
$ mbtcp-client -H 192.168.1.10 write-coil 5 on
$ mbtcp-client -H 192.168.1.10 write-read 0 20 22 1 -n 4     # write @0..2, then read @0..3 in one request
$ mbtcp-client -H 192.168.1.10 read-holding 0 -n 2 -T float32         # 2 float32 values: registers @0..3
$ mbtcp-client -H 192.168.1.10 write-registers 0 3.14 -T float32 -b CDAB   # a float32, word swapped
$ mbtcp-client -H 192.168.1.10 device-id

Values accept the decimal, hexadecimal (0x...), octal (0o...) and binary (0b...) notations. Coil values accept
1/0, on/off, true/false and yes/no (any case).

VALUES ON SEVERAL REGISTERS (-T/--type, -b/--byte-order)
--------------------------------------------------------
read-holding, read-input-regs, write-registers and write-read read and write typed values: uint16, int16 (1
register), uint32, int32, float32 (2 registers), uint64, int64, float64 (4 registers). -n then counts the values,
not the registers.

The Modbus specification only defines 16 bits registers (big endian): each device chooses how it stores a value
on several registers. -b/--byte-order uses the notation of most Modbus software, A being the most significant byte:

    ABCD  big endian (default): the first register holds the most significant word
    CDAB  word swap: the first register holds the least significant word (common on PLCs)
    BADC  byte swap: the bytes of each register are swapped
    DCBA  little endian: word swap and byte swap

For a 64 bits value, CDAB reverses the order of the 4 registers. The float32 3.14 (0x4048F5C3) is stored as
4048 F5C3 in ABCD, F5C3 4048 in CDAB, 4840 C3F5 in BADC and C3F5 4840 in DCBA: try the orders on a known value.

A negative value in exponent notation (-1e-3), -inf or -nan looks like an option: write the values after "--"
($ mbtcp-client write-registers 0 -T float32 -- -1e-3 -inf).

OUTPUT FORMATS (-f/--format)
----------------------------
table  for a human: a header (server, request, response time) and a table. Each row shows the offset from the
       first address, then the address in decimal and hexadecimal. Registers are shown in hexadecimal, unsigned,
       signed, binary and ASCII; bits in a grid of 10 per row. With -i, the screen is refreshed in place and the
       values that changed since the previous read are highlighted.
plain  one line per item, "address value" (--hex and --signed choose the notation of the registers). With
       -T, the address is the one of the first register of the value, --hex gives the bits of the value.
csv    "timestamp,address,value" lines, with a header line. A device-id text that a spreadsheet would read as a
       formula (it starts with =, +, - or @) gets a leading "'".
json   one JSON object per read (JSON Lines with -i).
auto   (default) table when the output is a terminal, plain otherwise (a pipe, a file): scripts get a stable output.

Colors are off with --no-color, the NO_COLOR environment variable, or when the output is not a terminal.

EXIT STATUS
-----------
0 success (or Ctrl-C to stop a read repeated with -i), 1 request failed (network error, timeout, Modbus
exception...), 2 invalid parameter, 130 request interrupted by Ctrl-C.
"""

import argparse
import csv
import datetime
import json
import logging
import math
import os
import shutil
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TextIO

from ..client import ModbusClient
from ..codec import ByteOrder, Codec, DataType
from ..constants import MODBUS_PORT
from ._common import add_version_arg, positive_float, tcp_port, unit_id

logger = logging.getLogger('mbtcp-client')

_TRUE_WORDS = ('1', 'on', 'true', 'yes')
_FALSE_WORDS = ('0', 'off', 'false', 'no')
# the coil values for the help and the errors, from the lists above: "1/0, on/off, true/false or yes/no"
_COIL_WORDS_TXT = ', '.join(f'{t}/{f}' for t, f in zip(_TRUE_WORDS[:-1], _FALSE_WORDS[:-1])) + \
    f' or {_TRUE_WORDS[-1]}/{_FALSE_WORDS[-1]}'

FORMATS = ('auto', 'table', 'plain', 'csv', 'json')

# a value that changed stays highlighted at least this time (s) with -i, a fast refresh doesn't hide a change
HIGHLIGHT_TIME = 3.0

# number of bits per row of the table of coils and discrete inputs (decimal addresses: rows 100, 110, 120...)
BITS_PER_ROW = 10

# names of the standard objects of the device identification (0x07 to 0x7F are reserved, 0x80 to 0xFF private)
DEVICE_ID_OBJECTS = {
    0x00: 'VendorName',
    0x01: 'ProductCode',
    0x02: 'MajorMinorRevision',
    0x03: 'VendorUrl',
    0x04: 'ProductName',
    0x05: 'ModelName',
    0x06: 'UserApplicationName',
}

# what a read command returns, for the header of the table
_READ_KINDS = {
    'read-coils': 'coil',
    'read-inputs': 'discrete input',
    'read-holding': 'holding register',
    'read-input-regs': 'input register',
    'write-read': 'holding register',
}


def int_value(value: str) -> int:
    """argparse type: an int in any Python notation (42, 0x2a, 0o52, 0b101010)."""
    try:
        return int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an integer') from None


def bool_value(value: str) -> bool:
    """argparse type: a coil value."""
    if value.lower() in _TRUE_WORDS:
        return True
    if value.lower() in _FALSE_WORDS:
        return False
    raise argparse.ArgumentTypeError(f'{value!r} is not a coil value (use {_COIL_WORDS_TXT})')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='mbtcp-client', description='Modbus/TCP client for the command line',
                                     epilog='Exit status: 0 success, 1 request failed, 2 invalid parameter, '
                                            '130 interrupted (Ctrl-C).')
    parser.add_argument('-H', '--host', default='localhost', help='server host (default: localhost)')
    parser.add_argument('-p', '--port', type=tcp_port, default=MODBUS_PORT,
                        help=f'server TCP port (default: {MODBUS_PORT})')
    parser.add_argument('-u', '--unit-id', type=unit_id, default=1, help='unit id, 0 to 255 (default: 1)')
    parser.add_argument('-t', '--timeout', type=positive_float, default=5.0,
                        help='request timeout in s (default: 5.0)')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode (dump every frame)')
    add_version_arg(parser)
    sub = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    # options of the commands that print a result
    output = argparse.ArgumentParser(add_help=False)
    output.add_argument('-f', '--format', choices=FORMATS, default='auto',
                        help='output format: table for a human, plain ("address value" lines), csv or json '
                             '(default: auto, table on a terminal, plain otherwise)')
    output.add_argument('--no-color', action='store_true', help='no colors in the table (also set by NO_COLOR)')

    # options of the commands on registers: values on several registers
    typed = argparse.ArgumentParser(add_help=False)
    typed.add_argument('-T', '--type', choices=[t.value for t in DataType], default=None,
                       help='type of the values: uint16 (default), int16, uint32, int32, float32 (2 registers), '
                            'uint64, int64, float64 (4 registers)')
    typed.add_argument('-b', '--byte-order', choices=[o.value for o in ByteOrder], default=None,
                       help='order of the bytes of a value on the registers, A is the most significant byte: '
                            'ABCD big endian (default), CDAB word swap, BADC byte swap, DCBA little endian')

    # read requests
    reads = (('read-coils', 'read coils (function 0x01)', 2000),
             ('read-inputs', 'read discrete inputs (function 0x02)', 2000),
             ('read-holding', 'read holding registers (function 0x03)', 125),
             ('read-input-regs', 'read input registers (function 0x04)', 125))
    for name, help_txt, max_nb in reads:
        registers = 'coils' not in name and 'inputs' not in name
        p = sub.add_parser(name, help=help_txt, description=help_txt, parents=[output] + ([typed] if registers else []))
        p.add_argument('address', type=int_value, help='first address (0 to 65535)')
        p.add_argument('-n', '--number', type=int_value, default=1,
                       help=f'number of items (1 to {max_nb})' + (', or of values with -T' if registers else ''))
        if registers:
            p.add_argument('--hex', action='store_true',
                           help='values in hexadecimal (plain, csv and json formats: the table shows them all)')
            p.add_argument('--signed', action='store_true',
                           help='values as signed 16 bits integers (plain, csv and json formats)')
            p.set_defaults(command_parser=p)
        p.add_argument('-i', '--interval', type=positive_float, default=None,
                       help='repeat the read every INTERVAL s, until Ctrl-C')

    # write requests
    txt = 'write a single coil (function 0x05)'
    p = sub.add_parser('write-coil', help=txt, description=txt)
    p.add_argument('address', type=int_value, help='coil address (0 to 65535)')
    p.add_argument('value', type=bool_value, help=_COIL_WORDS_TXT)
    txt = 'write multiple coils (function 0x0F)'
    p = sub.add_parser('write-coils', help=txt, description=txt)
    p.add_argument('address', type=int_value, help='first coil address (0 to 65535)')
    p.add_argument('values', type=bool_value, nargs='+', help=f'{_COIL_WORDS_TXT} (1 to 1968 values)')
    txt = 'write a single holding register (function 0x06)'
    p = sub.add_parser('write-register', help=txt, description=txt)
    p.add_argument('address', type=int_value, help='register address (0 to 65535)')
    p.add_argument('value', type=int_value, help='value (0 to 65535, or -32768 to -1 for a signed value)')
    txt = 'write multiple holding registers (function 0x10)'
    p = sub.add_parser('write-registers', help=txt, description=txt, parents=[typed])
    p.add_argument('address', type=int_value, help='first register address (0 to 65535)')
    p.add_argument('values', nargs='+',
                   help='values (0 to 65535, or -32768 to -1 for a signed value, 1 to 123 registers), '
                        'of the type of -T')
    p.set_defaults(command_parser=p)

    # write then read in one request
    txt = 'write then read holding registers in one request (function 0x17)'
    p = sub.add_parser('write-read', help=txt, parents=[output, typed],
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       description=txt + '\n\nThe server writes the values, then reads the registers, in this order '
                                         'and in one request: the\nregisters read include the values just written (and '
                                         'what the server did with them).',
                       epilog='examples:\n'
                              '  write-read 0 20 22 1          write @0..2, read @0..2 back\n'
                              '  write-read 0 20 22 1 -n 4     write @0..2, read @0..3\n'
                              '  write-read 10 7 -r 100 -n 2   write @10, read @100..101\n'
                              '  write-read 0 1.5 -T float32   write the float32 1.5 @0..1, read it back\n'
                              '\nIn the table, the "Written" column gives the value written at each address, and '
                              '"!" marks\na register whose value read differs from the value written.')
    p.add_argument('address', type=int_value, help='first register address of the write (0 to 65535)')
    p.add_argument('values', nargs='+',
                   help='values to write (0 to 65535, or -32768 to -1 for a signed value, 1 to 121 registers), '
                        'of the type of -T')
    p.add_argument('-r', '--read', type=int_value, default=None, metavar='ADDRESS',
                   help='first register address of the read (default: the address of the write)')
    p.add_argument('-n', '--number', type=int_value, default=None,
                   help='number of registers (or of values with -T) to read, 1 to 125 registers '
                        '(default: the number of values written)')
    p.add_argument('--hex', action='store_true',
                   help='values in hexadecimal (plain, csv and json formats: the table shows them all)')
    p.add_argument('--signed', action='store_true',
                   help='values as signed 16 bits integers (plain, csv and json formats)')
    # its own parser, for the errors of check_args() (with the usage of the command)
    p.set_defaults(command_parser=p)

    # device identification
    txt = 'read device identification (function 0x2B/0x0E)'
    p = sub.add_parser('device-id', help=txt, description=txt, parents=[output])
    p.add_argument('-c', '--code', type=int_value, choices=(1, 2, 3, 4), default=1,
                   help='1 basic, 2 regular, 3 extended (stream access), 4 one object (default: 1)')
    p.add_argument('-o', '--object-id', type=int_value, default=0, help='first (or only) object id (default: 0)')
    return parser


# --- values ---

def to_unsigned(value: int) -> int:
    """Return a register value as unsigned 16 bits: -32768 to -1 are mapped to 32768 to 65535 (2's complement)."""
    if -0x8000 <= value < 0:
        return value + 0x10000
    return value


def to_signed(value: int) -> int:
    """Return an unsigned 16 bits register value as a signed one."""
    return value - 0x10000 if value >= 0x8000 else value


def codec_of(args: argparse.Namespace) -> Codec | None:
    """The codec of the values with -T/-b (set by check_args()), None for the 16 bits registers."""
    codec: Codec | None = getattr(args, 'codec', None)
    return codec


def format_value(value: object, args: argparse.Namespace) -> str:
    """A value in the notation of the plain and csv formats."""
    if isinstance(value, bool):
        return '1' if value else '0'
    codec = codec_of(args)
    if codec is not None and isinstance(value, (int, float)):
        return codec.hex(value) if getattr(args, 'hex', False) else codec.text(value)
    if isinstance(value, int):
        if getattr(args, 'hex', False):
            return f'0x{value:04X}'
        if getattr(args, 'signed', False):
            return str(to_signed(value))
    return str(value)


def json_value(value: object, args: argparse.Namespace) -> object:
    """A value for the json format: a bool for a bit, an int (or a "0x..." string with --hex) for a register."""
    if isinstance(value, bool):
        return value
    codec = codec_of(args)
    if codec is not None and isinstance(value, (int, float)):
        if getattr(args, 'hex', False):
            return codec.hex(value)
        if not isinstance(value, float):
            return value
        # the shortest float of the text (0.1, not 0.10000000149 for a float32); JSON has no NaN or infinity: a
        # string, like "nan" or "inf"
        return float(codec.text(value)) if math.isfinite(value) else str(value)
    if isinstance(value, int):
        if getattr(args, 'hex', False):
            return f'0x{value:04X}'
        if getattr(args, 'signed', False):
            return to_signed(value)
    return value


def address_hex(address: int) -> str:
    """An address in hexadecimal for the table: "0x000A" (4 digits, like the register values)."""
    return f'0x{address:04X}'


def register_ascii(value: int) -> str:
    """The 2 bytes of a register (big endian) as ASCII chars, "." for a non printable byte."""
    return ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in value.to_bytes(2, 'big'))


def register_binary(value: int) -> str:
    """A register in binary, in groups of 4 bits: "0001 0010 0011 0100"."""
    bits = f'{value:016b}'
    return ' '.join(bits[i:i + 4] for i in range(0, 16, 4))


def safe_text(value: bytes) -> str:
    """An object of the device identification as text: never print raw control chars sent by a server."""
    text = value.decode('ascii', errors='backslashreplace')
    return ''.join(c if c.isprintable() else f'\\x{ord(c):02x}' for c in text)


# a cell that starts with one of these chars is a formula for a spreadsheet (CWE-1236)
CSV_FORMULA_CHARS = ('=', '+', '-', '@', '\t', '\r', '\n')


def csv_text(text: str) -> str:
    """A text sent by a server, for a csv cell: never read as a formula by a spreadsheet.

    A text that starts with =, +, -, @ (or a tab, CR, LF) gets a leading "'" (like =HYPERLINK(...) or
    =cmd|...): the spreadsheet shows it as text. Only for the texts of a server: the values read are numbers.
    """
    return "'" + text if text.startswith(CSV_FORMULA_CHARS) else text


def device_object_name(obj_id: int) -> str:
    if obj_id in DEVICE_ID_OBJECTS:
        return DEVICE_ID_OBJECTS[obj_id]
    return 'reserved' if obj_id < 0x80 else 'private'


# --- terminal ---

def enable_ansi(stream: TextIO) -> bool:
    """Return True if stream is a terminal that understands the ANSI escape sequences (colors, cursor moves)."""
    try:
        if not stream.isatty():
            return False
    except (AttributeError, ValueError):  # no isatty(), or a closed stream
        return False
    if sys.platform == 'win32':
        # the Windows console needs the "virtual terminal" mode (on by default in Windows Terminal)
        try:
            import ctypes
            import msvcrt
            kernel32 = ctypes.windll.kernel32
            handle = msvcrt.get_osfhandle(stream.fileno())
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except (ImportError, AttributeError, OSError):
            return False
    return os.environ.get('TERM') != 'dumb'


def can_encode(stream: TextIO, text: str) -> bool:
    """Return True if the encoding of stream can write text (like box drawing chars on a legacy console)."""
    try:
        text.encode(getattr(stream, 'encoding', None) or 'ascii')
    except (UnicodeEncodeError, LookupError):
        return False
    return True


class Style:
    """ANSI styles of the table, or plain text when disabled."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def _sgr(self, text: str, code: str) -> str:
        return f'\x1b[{code}m{text}\x1b[0m' if self.enabled and text else text

    def bold(self, text: str) -> str:
        return self._sgr(text, '1')

    def dim(self, text: str) -> str:
        return self._sgr(text, '2')

    def red(self, text: str) -> str:
        return self._sgr(text, '1;31')

    def green(self, text: str) -> str:
        return self._sgr(text, '32')

    def cyan(self, text: str) -> str:
        return self._sgr(text, '36')

    def changed(self, text: str) -> str:
        """A value changed since the previous read."""
        return self._sgr(text, '1;4;33')


# --- output ---

@dataclass
class ReadResult:
    """The values of a successful read, with the time of the request (for the header and the csv/json formats)."""
    address: int
    values: Sequence[object]
    timestamp: float
    duration: float
    # with -T: the registers read, and the number of registers of a value
    registers: Sequence[int] = ()
    step: int = 1

    def item_registers(self, index: int) -> tuple[int, ...]:
        """The registers of the value at index (empty without -T)."""
        return tuple(self.registers[index * self.step:(index + 1) * self.step])


class Output:
    """Print the results of the commands in the format chosen by -f/--format."""

    def __init__(self, args: argparse.Namespace, stream: TextIO | None = None) -> None:
        self.args = args
        self.codec = codec_of(args)
        self.stream = sys.stdout if stream is None else stream
        ansi = enable_ansi(self.stream)
        fmt = getattr(args, 'format', 'auto')
        self.format = ('table' if ansi else 'plain') if fmt == 'auto' else fmt
        no_color = getattr(args, 'no_color', False) or bool(os.environ.get('NO_COLOR'))
        self.style = Style(ansi and not no_color)
        # with -i on a terminal, the table is redrawn in place (not with -d: the frames dumps would mix with it)
        self.live = (self.format == 'table' and ansi and getattr(args, 'interval', None) is not None
                     and not args.debug)
        unicode = can_encode(self.stream, '─·')
        self.rule_char = '─' if unicode else '-'
        self.sep = ' · ' if unicode else ' | '
        # the values of the previous read (the registers of each value with -T: a NaN read twice is not a change)
        self.previous: list[object] | None = None
        # offset of an item -> time of its last change (time.monotonic()), for the highlights of the table
        self.changed_at: dict[int, float] = {}
        self.last_good: float | None = None
        self.last_result: ReadResult | None = None
        self.reads = 0
        self.errors = 0
        self.csv_header = False

    def write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    # header of the table

    def server_txt(self) -> str:
        host = f'[{self.args.host}]' if ':' in self.args.host else self.args.host
        return f'{host}:{self.args.port}'

    def header(self, title: str, detail: str, duration: float | None) -> list[str]:
        s = self.style
        parts = [s.bold(s.cyan(title)), self.server_txt(), f'unit {self.args.unit_id}']
        if detail:
            parts.append(detail)
        if duration is not None:
            parts.append(f'{duration * 1000:.1f} ms')
        lines = [s.dim(self.sep).join(parts)]
        if getattr(self.args, 'interval', None) is not None:
            now = datetime.datetime.now().strftime('%H:%M:%S')
            status = [now, f'read #{self.reads}', f'every {self.args.interval:g} s']
            if self.errors:
                status.append(s.red(f'{self.errors} error{"s" if self.errors > 1 else ""}'))
            status.append('Ctrl-C to stop')
            lines.append(s.dim(self.sep).join(status))
        return lines

    def read_detail(self, address: int, number: int) -> str:
        """The items read: "4 holding registers @ 0..3", or with -T "2 float32 CDAB @ 0..3" (the registers)."""
        kind = _READ_KINDS[self.args.command]
        plural = 's' if number > 1 else ''
        if self.codec is not None:
            span = span_txt(address, number * self.codec.words)
            return f'{number} {self.codec.data_type.value} {self.codec.byte_order.value} @ {span}'
        return f'{number} {kind}{plural} @ {span_txt(address, number)}'

    def result_detail(self, res: ReadResult) -> str:
        """The detail of the header: the registers read (and written, for write-read)."""
        detail = self.read_detail(res.address, len(res.values))
        if self.args.command != 'write-read':
            return detail
        n_written = len(self.args.values)
        return f'{n_written} written @ {span_txt(self.args.address, len(self.args.registers))}{self.sep}{detail} read'

    def json_read(self, res: ReadResult) -> dict[str, object]:
        """The json object of a read (with the values written, for write-read)."""
        obj: dict[str, object] = {'timestamp': iso_time(res.timestamp), 'command': self.args.command,
                                  'address': res.address}
        if self.codec is not None:
            obj.update(type=self.codec.data_type.value, byte_order=self.codec.byte_order.value)
        obj['values'] = [json_value(v, self.args) for v in res.values]
        if self.args.command == 'write-read':
            written = self.args.values if self.codec is not None else [to_unsigned(v) for v in self.args.values]
            obj['written'] = {'address': self.args.address, 'values': [json_value(v, self.args) for v in written]}
        return obj

    def rule(self, widths: Sequence[int]) -> str:
        return self.style.dim('  '.join(self.rule_char * w for w in widths))

    # tables

    def changed(self, offset: int) -> bool:
        """Return True if the item at offset changed recently (see HIGHLIGHT_TIME)."""
        when = self.changed_at.get(offset)
        return when is not None and time.monotonic() - when < HIGHLIGHT_TIME

    def written_values(self) -> dict[int, tuple[tuple[int, ...], str]]:
        """write-read: address of a value -> (its registers, its text in the table); empty for the other commands."""
        if self.args.command != 'write-read':
            return {}
        if self.codec is None:
            return {self.args.address + i: ((r,), f'0x{r:04X}') for i, r in enumerate(self.args.registers)}
        words = self.codec.words
        return {self.args.address + i * words: (tuple(self.args.registers[i * words:(i + 1) * words]),
                                                self.codec.text(v)) for i, v in enumerate(self.args.values)}

    def value_cells(self, res: ReadResult, index: int) -> tuple[str, ...]:
        """The cells of a value in the table (after the address and written columns)."""
        value = res.values[index]
        if self.codec is not None and isinstance(value, (int, float)):
            regs = ' '.join(f'{r:04X}' for r in res.item_registers(index))
            return self.codec.text(value), self.codec.hex(value), regs
        assert isinstance(value, int)
        return f'0x{value:04X}', str(value), str(to_signed(value)), register_binary(value), register_ascii(value)

    def mark(self, changed: bool, differs: bool) -> str:
        """The mark of a row (also seen without colors): "*" a value changed since the previous read, "!" a value read
        that differs from the value written."""
        s = self.style
        if changed:
            return s.changed('*') if s.enabled else '*'
        return s.bold('!') if differs else ' '

    def register_table(self, res: ReadResult) -> list[str]:
        s = self.style
        written = self.written_values()
        # write-read: a "Written" column after the address columns
        extra = 1 if written else 0
        if self.codec is not None:
            # the hex of the value is in the ABCD order, the registers as read (in the byte order of the device)
            value_heads: tuple[str, ...] = ('Value', 'Hex', 'Registers')
            n_left = 1
        else:
            value_heads = ('Hex', 'Unsigned', 'Signed', 'Binary', 'ASCII')
            n_left = 2
        heads = ('Offset', 'Address', 'Addr hex') + (('Written',) if written else ()) + value_heads
        step = res.step
        rows: list[tuple[str, ...]] = []
        for index in range(len(res.values)):
            address = res.address + index * step
            written_cell = (written[address][1] if address in written else '',) if written else ()
            rows.append((str(index * step), str(address), address_hex(address)) + written_cell
                        + self.value_cells(res, index))
        widths = [max(len(heads[i]), *(len(r[i]) for r in rows)) for i in range(len(heads))]
        right = (True,) * (len(heads) - n_left) + (False,) * n_left

        def cell(text: str, i: int) -> str:
            return text.rjust(widths[i]) if right[i] else text.ljust(widths[i])

        lines = ['  ' + s.bold('  '.join(cell(h, i) for i, h in enumerate(heads)).rstrip()),
                 '  ' + self.rule(widths)]
        differs = 0
        for index, row in enumerate(rows):
            address = res.address + index * step
            registers = res.item_registers(index) or (res.values[index],)
            changed = self.changed(index)
            # the last column without padding (no trailing spaces)
            cells = [cell(t, i) for i, t in enumerate(row[:-1])] + [row[-1]]
            # the offset, the address and the written columns are never highlighted, only the value
            text = '  '.join(cells[3 + extra:])
            if changed:
                text = s.changed(text)
            elif not any(registers):
                text = s.dim(text)
            prefix = s.dim(cells[0]) + '  ' + cells[1] + '  ' + s.dim(cells[2]) + '  '
            if written:
                prefix += s.cyan(cells[3]) + '  '
            differ = not changed and address in written and written[address][0] != registers
            differs += differ
            lines.append(self.mark(changed, differ) + ' ' + prefix + text)
        if differs:
            what = 'value' if self.codec is not None else 'register'
            lines.append('')
            lines.append('  ' + s.dim(f'! {differs} {what}{"s" if differs > 1 else ""} read with another value '
                                      'than the one written (changed by the server in the same request)'))
        return lines

    def bit_table(self, res: ReadResult) -> list[str]:
        s = self.style
        values = [bool(v) for v in res.values]
        n_cols = min(BITS_PER_ROW, len(values))
        addr_w = max(len('Address'), len(str(res.address + len(values) - 1)))
        hex_w = max(len('Addr hex'), len(address_hex(res.address + len(values) - 1)))
        off_w = max(len('Offset'), len(str(len(values) - 1)))

        def group(cells: list[str]) -> str:
            # a wider gap in the middle of the row, to count the bits at a glance
            half = BITS_PER_ROW // 2
            return '  '.join([' '.join(cells[:half]), ' '.join(cells[half:])]) if len(cells) > half else \
                ' '.join(cells)

        heads = [f'+{i}'.rjust(2) for i in range(n_cols)]
        head_txt = group(heads)
        lines = ['  ' + s.bold('Offset'.rjust(off_w) + '  ' + 'Address'.rjust(addr_w) + '  '
                               + 'Addr hex'.rjust(hex_w) + '  ' + head_txt),
                 '  ' + self.rule([off_w, addr_w, hex_w, len(head_txt)])]
        for row_start in range(0, len(values), BITS_PER_ROW):
            cells = []
            for offset in range(row_start, min(row_start + BITS_PER_ROW, len(values))):
                bit = values[offset]
                changed = self.changed(offset)
                txt = '1' if bit else '0'
                if changed:
                    cells.append(s.changed(f' {txt}') if s.enabled else f'*{txt}')
                else:
                    cells.append(' ' + (s.green(s.bold(txt)) if bit else s.dim(txt)))
            row_addr = res.address + row_start
            lines.append('  ' + s.dim(str(row_start).rjust(off_w)) + '  ' + str(row_addr).rjust(addr_w)
                         + '  ' + s.dim(address_hex(row_addr).rjust(hex_w)) + '  ' + group(cells))
        on = sum(values)
        lines.append('')
        lines.append('  ' + s.dim(f'{on} on, {len(values) - on} off'))
        return lines

    def render_screen(self, lines: list[str]) -> None:
        if self.live:
            # redraw from the top left corner (no flicker), cut what doesn't fit in the terminal
            height = shutil.get_terminal_size().lines
            if len(lines) > height - 1:
                hidden = len(lines) - (height - 2)
                lines = lines[:height - 2] + [self.style.dim(f'... {hidden} more lines (enlarge the terminal)')]
            self.write('\x1b[H' + ''.join(line + '\x1b[K\n' for line in lines) + '\x1b[J')
        else:
            if self.reads > 1 or self.errors:
                lines = [''] + lines
            self.write('\n'.join(lines) + '\n')

    # results of the commands

    def read(self, res: ReadResult) -> None:
        self.reads += 1
        values = list(res.values)
        # compare the registers of the values with -T (a NaN is never equal to itself)
        keys: list[object] = [res.item_registers(i) for i in range(len(values))] if res.registers else values
        if self.previous is not None:
            now = time.monotonic()
            for index, key in enumerate(keys):
                if index < len(self.previous) and self.previous[index] != key:
                    self.changed_at[index] = now
        if self.format == 'plain':
            for index, value in enumerate(values):
                self.write(f'{res.address + index * res.step} {format_value(value, self.args)}\n')
        elif self.format == 'csv':
            writer = csv.writer(self.stream, lineterminator='\n')
            if not self.csv_header:
                writer.writerow(('timestamp', 'address', 'value'))
                self.csv_header = True
            stamp = iso_time(res.timestamp)
            for index, value in enumerate(values):
                writer.writerow((stamp, res.address + index * res.step, format_value(value, self.args)))
            self.stream.flush()
        elif self.format == 'json':
            obj = self.json_read(res)
            self.write(json.dumps(obj) + '\n')
        else:
            lines = self.header(self.args.command, self.result_detail(res), res.duration)
            lines.append('')
            is_bits = self.args.command in ('read-coils', 'read-inputs')
            lines += self.bit_table(res) if is_bits else self.register_table(res)
            self.render_screen(lines)
        self.previous = keys
        self.last_good = res.timestamp
        self.last_result = res

    def read_error(self, msg: str) -> None:
        """A failed read repeated with -i: the loop goes on."""
        self.errors += 1
        if not self.live:
            print(f'error: {msg}', file=sys.stderr)
            return
        s = self.style
        lines = self.header(self.args.command, self.read_detail(self.args.address, self.args.number), None)
        lines += ['', '  ' + s.red('error: ') + msg]
        if self.last_result is not None:
            when = datetime.datetime.fromtimestamp(self.last_result.timestamp).strftime('%H:%M:%S')
            lines.append('  ' + s.dim(f'last good read at {when}, the values below are not updated'))
            lines.append('')
            res = self.last_result
            saved, self.changed_at = self.changed_at, {}  # nothing to highlight in stale values
            is_bits = self.args.command in ('read-coils', 'read-inputs')
            lines += [s.dim(line) for line in (self.bit_table(res) if is_bits else self.register_table(res))]
            self.changed_at = saved
        self.render_screen(lines)

    def written(self, count: int, duration: float) -> None:
        """Success of a write: a confirmation line on a terminal only (nothing in a pipe, like before)."""
        if self.format != 'table':
            return
        s = self.style
        kind = 'coil' if 'coil' in self.args.command else 'register'
        plural = 's' if count > 1 else ''
        span = span_txt(self.args.address, count)
        parts = [s.green(s.bold('OK')) + ' ' + s.bold(self.args.command), self.server_txt(),
                 f'unit {self.args.unit_id}', f'{count} {kind}{plural} written @ {span}', f'{duration * 1000:.1f} ms']
        self.write(s.dim(self.sep).join(parts) + '\n')

    def device_id(self, objects: list[tuple[int, bytes]], conformity: int, duration: float) -> None:
        if self.format == 'plain':
            for obj_id, value in objects:
                self.write(f'0x{obj_id:02X} {safe_text(value)}\n')
        elif self.format == 'csv':
            writer = csv.writer(self.stream, lineterminator='\n')
            writer.writerow(('id', 'name', 'value'))
            for obj_id, value in objects:
                writer.writerow((f'0x{obj_id:02X}', device_object_name(obj_id), csv_text(safe_text(value))))
            self.stream.flush()
        elif self.format == 'json':
            # json.dumps escapes the control chars: the decoded text is kept as is
            obj = {'conformity_level': conformity,
                   'objects': [{'id': obj_id, 'name': device_object_name(obj_id),
                                'value': value.decode('ascii', errors='backslashreplace')}
                               for obj_id, value in objects]}
            self.write(json.dumps(obj) + '\n')
        else:
            s = self.style
            plural = 's' if len(objects) > 1 else ''
            lines = self.header('device-id', f'{len(objects)} object{plural}{self.sep}'
                                f'conformity 0x{conformity:02X}', duration)
            rows = [(f'0x{obj_id:02X}', device_object_name(obj_id), safe_text(value)) for obj_id, value in objects]
            heads = ('Id', 'Object', 'Value')
            widths = [max(len(heads[i]), *(len(r[i]) for r in rows)) if rows else len(heads[i]) for i in range(3)]
            lines += ['', '  ' + s.bold('  '.join(h.ljust(widths[i]) for i, h in enumerate(heads))),
                      '  ' + self.rule(widths)]
            for obj_id_txt, name, text in rows:
                lines.append('  ' + s.dim(obj_id_txt.ljust(widths[0])) + '  ' + name.ljust(widths[1]) + '  ' + text)
            self.render_screen(lines)


def span_txt(address: int, count: int) -> str:
    """A range of addresses: "10", or "10..13"."""
    return f'{address}' if count == 1 else f'{address}..{address + count - 1}'


def iso_time(timestamp: float) -> str:
    """A local time with the UTC offset and the milliseconds: "2026-10-17T13:20:05.123+02:00"."""
    return datetime.datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec='milliseconds')


# --- commands ---

def do_read(client: ModbusClient, args: argparse.Namespace, out: Output) -> bool:
    read_funcs: dict[str, Callable[[int, int], Sequence[object] | None]] = {
        'read-coils': client.read_coils,
        'read-inputs': client.read_discrete_inputs,
        'read-holding': client.read_holding_registers,
        'read-input-regs': client.read_input_registers,
    }
    codec = codec_of(args)
    step = 1 if codec is None else codec.words
    timestamp = time.time()
    start = time.monotonic()
    values = read_funcs[args.command](args.address, args.number * step)
    duration = time.monotonic() - start
    if values is None:
        return False
    out.read(typed_result(codec, args.address, values, timestamp, duration))
    return True


def typed_result(codec: Codec | None, address: int, values: Sequence[object], timestamp: float,
                 duration: float) -> ReadResult:
    """The result of a read: the registers read, or the values of their type with -T."""
    if codec is None:
        return ReadResult(address, values, timestamp, duration)
    registers = [v for v in values if isinstance(v, int)]
    return ReadResult(address, codec.decode(registers), timestamp, duration, registers, codec.words)


def do_write(client: ModbusClient, args: argparse.Namespace, out: Output) -> bool:
    start = time.monotonic()
    if args.command == 'write-coil':
        ok, count = client.write_single_coil(args.address, args.value), 1
    elif args.command == 'write-coils':
        ok, count = client.write_multiple_coils(args.address, args.values), len(args.values)
    elif args.command == 'write-register':
        ok, count = client.write_single_register(args.address, to_unsigned(args.value)), 1
    else:
        ok, count = client.write_multiple_registers(args.address, args.registers), len(args.registers)
    if ok:
        out.written(count, time.monotonic() - start)
    return ok


def do_write_read(client: ModbusClient, args: argparse.Namespace, out: Output) -> bool:
    """Function 0x17: the server writes, then reads, in one request."""
    codec = codec_of(args)
    read_address, read_count = write_read_span(args)
    timestamp = time.time()
    start = time.monotonic()
    values = client.write_read_multiple_registers(args.address, args.registers, read_address, read_count)
    duration = time.monotonic() - start
    if values is None:
        return False
    out.read(typed_result(codec, read_address, values, timestamp, duration))
    return True


def write_read_span(args: argparse.Namespace) -> tuple[int, int]:
    """write-read: the first address and the number of registers of the read."""
    codec = codec_of(args)
    step = 1 if codec is None else codec.words
    number = len(args.values) if args.number is None else args.number
    return (args.address if args.read is None else args.read), number * step


def do_device_id(client: ModbusClient, args: argparse.Namespace, out: Output) -> bool:
    start = time.monotonic()
    response = client.read_device_identification(read_code=args.code, object_id=args.object_id)
    duration = time.monotonic() - start
    if response is None:
        return False
    out.device_id(sorted(response.objects_by_id.items()), response.conformity_level, duration)
    if response.more_follows:
        print(f'# more objects available: use -o 0x{response.next_object_id:02X}', file=sys.stderr)
    return True


def error_txt(client: ModbusClient) -> str:
    msg = client.last_error_as_txt
    if client.last_except:
        msg += f': exception 0x{client.last_except:02X} ({client.last_except_as_txt})'
    return msg


def print_error(client: ModbusClient) -> None:
    print(f'error: {error_txt(client)}', file=sys.stderr)


def run_command(client: ModbusClient, args: argparse.Namespace, out: Output | None = None) -> bool:
    """Run the command of args, return True on success (read commands with an interval loop until Ctrl-C)."""
    if out is None:
        out = Output(args)
    if args.command.startswith('read-'):
        while True:
            ok = do_read(client, args, out)
            if args.interval is None:
                return ok
            if not ok:
                out.read_error(error_txt(client))
            time.sleep(args.interval)
    if args.command == 'write-read':
        return do_write_read(client, args, out)
    if args.command.startswith('write-'):
        return do_write(client, args, out)
    return do_device_id(client, args, out)


def check_args(args: argparse.Namespace) -> None:
    """The commands on registers: check the options and the values before any request, with a clear message (exit
    status 2 and the usage of the command), set args.codec (with -T/-b) and args.registers (the registers to write).
    """
    parser: argparse.ArgumentParser | None = getattr(args, 'command_parser', None)
    if parser is None:
        return
    args.codec = None
    if args.type is not None or args.byte_order is not None:
        if getattr(args, 'signed', False):
            parser.error('--signed cannot be used with -T/-b: use -T int16')
        args.codec = Codec(DataType(args.type or 'uint16'), ByteOrder(args.byte_order or 'ABCD'))
    codec: Codec | None = args.codec
    step = 1 if codec is None else codec.words
    if args.command.startswith('read-') and codec is not None and not 1 <= args.number * step <= 125:
        parser.error(f'-n/--number must be between 1 and {125 // step} '
                     f'({codec.data_type.value} values of {step} registers)')
    if args.command in ('write-registers', 'write-read'):
        args.registers = parse_values(args, parser, codec)
    if args.command == 'write-read':
        check_write_read(args, parser, step)


def parse_values(args: argparse.Namespace, parser: argparse.ArgumentParser, codec: Codec | None) -> list[int]:
    """The registers of the values to write (and args.values as numbers)."""
    try:
        if codec is not None:
            args.values = [codec.parse(text) for text in args.values]
            return codec.encode(args.values)
        args.values = [int_value(text) for text in args.values]
    except (ValueError, argparse.ArgumentTypeError) as e:
        parser.error(f'value {e}')
    for value in args.values:
        if not -0x8000 <= value <= 0xFFFF:
            parser.error(f'value {value} out of 0 to 65535 (or -32768 to -1 for a signed value)')
    return [to_unsigned(v) for v in args.values]


def check_write_read(args: argparse.Namespace, parser: argparse.ArgumentParser, step: int) -> None:
    """write-read: the ranges of the write and of the read."""
    unit = 'registers' if step == 1 else f'registers ({len(args.values)} values)'
    if not 1 <= len(args.registers) <= 121:
        parser.error(f'1 to 121 registers can be written, {len(args.registers)} {unit} given')
    if args.number is not None and not 1 <= args.number * step <= 125:
        parser.error(f'-n/--number must be between 1 and {125 // step} ({125 // step * step} registers read), '
                     f'not {args.number}')
    read_address, read_count = write_read_span(args)
    for name, address, count in (('write', args.address, len(args.registers)), ('read', read_address, read_count)):
        if not 0 <= address <= 0xFFFF:
            parser.error(f'{name} address {address} out of 0 to 65535')
        if address + count > 0x10000:
            parser.error(f'the {name} of {count} registers from @{address} ends after the last '
                         f'address (65535)')


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    check_args(args)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING, format='%(message)s')
    try:
        with ModbusClient(host=args.host, port=args.port, unit_id=args.unit_id, timeout=args.timeout) as client:
            ok = run_command(client, args)
    except (TypeError, ValueError) as e:
        # parameter refused by the library (unit id, address or number of items out of range...)
        parser.error(str(e))
    except KeyboardInterrupt:
        # the normal end of a read repeated with -i, but a single request interrupted before its end has failed
        # (like any program stopped by Ctrl-C: 128 + SIGINT)
        return 0 if getattr(args, 'interval', None) is not None else 130
    except BrokenPipeError:
        # output closed by the reader (like "| head"): stop quietly, without a traceback (stdout goes to devnull,
        # so that the flush at the exit of Python doesn't fail again)
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 1
    if not ok:
        print_error(client)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
