"""
mbtcp-client: a Modbus/TCP client for the command line
======================================================

Run it as "mbtcp-client" (pyModbusTCP-cli package) or "python -m pyModbusTCP.tools client".

Send one Modbus/TCP request (read or write) and print the result: one line per item, "address value".

USAGE
-----
$ mbtcp-client -H 192.168.1.10 read-holding 0 -n 4         # 4 holding registers from address 0
$ mbtcp-client -H 192.168.1.10 read-holding 0 -n 4 --hex   # same, values in hexadecimal
$ mbtcp-client -H 192.168.1.10 read-holding 0 -i 1.0       # read again every second (Ctrl-C to stop)
$ mbtcp-client -H 192.168.1.10 read-coils 100 -n 8
$ mbtcp-client -H 192.168.1.10 write-register 10 0x1234
$ mbtcp-client -H 192.168.1.10 write-registers 10 1 2 3
$ mbtcp-client -H 192.168.1.10 write-coil 5 on
$ mbtcp-client -H 192.168.1.10 device-id

Values accept the decimal, hexadecimal (0x...), octal (0o...) and binary (0b...) notations. Coil values accept
1/0, on/off and true/false.

EXIT STATUS
-----------
0 success, 1 request failed (network error, timeout, Modbus exception...), 2 invalid parameter.
"""

import argparse
import logging
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence

from ..client import ModbusClient

from ._common import add_version_arg, positive_float, tcp_port

logger = logging.getLogger('mbtcp-client')

_TRUE_WORDS = ('1', 'on', 'true', 'yes')
_FALSE_WORDS = ('0', 'off', 'false', 'no')


def int_value(value: str) -> int:
    """argparse type: an int in any Python notation (42, 0x2a, 0o52, 0b101010)."""
    try:
        return int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError('%r is not an integer' % value) from None


def bool_value(value: str) -> bool:
    """argparse type: a coil value."""
    if value.lower() in _TRUE_WORDS:
        return True
    if value.lower() in _FALSE_WORDS:
        return False
    raise argparse.ArgumentTypeError('%r is not a coil value (use 1/0, on/off or true/false)' % value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='mbtcp-client', description='Modbus/TCP client for the command line',
                                     epilog='Exit status: 0 success, 1 request failed, 2 invalid parameter.')
    parser.add_argument('-H', '--host', default='localhost', help='server host (default: localhost)')
    parser.add_argument('-p', '--port', type=tcp_port, default=502, help='server TCP port (default: 502)')
    parser.add_argument('-u', '--unit-id', type=int_value, default=1, help='unit id (default: 1)')
    parser.add_argument('-t', '--timeout', type=positive_float, default=5.0,
                        help='request timeout in s (default: 5.0)')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode (dump every frame)')
    add_version_arg(parser)
    sub = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    # read requests
    reads = (('read-coils', 'read coils (function 0x01)', 2000),
             ('read-inputs', 'read discrete inputs (function 0x02)', 2000),
             ('read-holding', 'read holding registers (function 0x03)', 125),
             ('read-input-regs', 'read input registers (function 0x04)', 125))
    for name, help_txt, max_nb in reads:
        p = sub.add_parser(name, help=help_txt, description=help_txt)
        p.add_argument('address', type=int_value, help='first address (0 to 65535)')
        p.add_argument('-n', '--number', type=int_value, default=1, help='number of items (1 to %d)' % max_nb)
        if 'coils' not in name and 'inputs' not in name:
            p.add_argument('--hex', action='store_true', help='print values in hexadecimal')
            p.add_argument('--signed', action='store_true', help='print values as signed 16 bits integers')
        p.add_argument('-i', '--interval', type=positive_float, default=None,
                       help='repeat the read every INTERVAL s, until Ctrl-C')

    # write requests
    p = sub.add_parser('write-coil', help='write a single coil (function 0x05)')
    p.add_argument('address', type=int_value, help='coil address (0 to 65535)')
    p.add_argument('value', type=bool_value, help='1/0, on/off or true/false')
    p = sub.add_parser('write-coils', help='write multiple coils (function 0x0F)')
    p.add_argument('address', type=int_value, help='first coil address (0 to 65535)')
    p.add_argument('values', type=bool_value, nargs='+', help='1/0, on/off or true/false (1 to 1968 values)')
    p = sub.add_parser('write-register', help='write a single holding register (function 0x06)')
    p.add_argument('address', type=int_value, help='register address (0 to 65535)')
    p.add_argument('value', type=int_value, help='value (0 to 65535, or -32768 to -1 for a signed value)')
    p = sub.add_parser('write-registers', help='write multiple holding registers (function 0x10)')
    p.add_argument('address', type=int_value, help='first register address (0 to 65535)')
    p.add_argument('values', type=int_value, nargs='+',
                   help='values (0 to 65535, or -32768 to -1 for a signed value, 1 to 123 values)')

    # device identification
    p = sub.add_parser('device-id', help='read device identification (function 0x2B/0x0E)')
    p.add_argument('-c', '--code', type=int, choices=(1, 2, 3, 4), default=1,
                   help='1 basic, 2 regular, 3 extended (stream access), 4 one object (default: 1)')
    p.add_argument('-o', '--object-id', type=int_value, default=0, help='first (or only) object id (default: 0)')
    return parser


def to_unsigned(value: int) -> int:
    """Return a register value as unsigned 16 bits: -32768 to -1 are mapped to 32768 to 65535 (2's complement)."""
    if -0x8000 <= value < 0:
        return value + 0x10000
    return value


def to_signed(value: int) -> int:
    """Return an unsigned 16 bits register value as a signed one."""
    return value - 0x10000 if value >= 0x8000 else value


def format_value(value: object, args: argparse.Namespace) -> str:
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, int):
        if getattr(args, 'hex', False):
            return '0x%04X' % value
        if getattr(args, 'signed', False):
            return str(to_signed(value))
    return str(value)


def do_read(client: ModbusClient, args: argparse.Namespace) -> bool:
    read_funcs: Dict[str, Callable[[int, int], Optional[Sequence[object]]]] = {
        'read-coils': client.read_coils,
        'read-inputs': client.read_discrete_inputs,
        'read-holding': client.read_holding_registers,
        'read-input-regs': client.read_input_registers,
    }
    values = read_funcs[args.command](args.address, args.number)
    if values is None:
        return False
    for offset, value in enumerate(values):
        print('%d %s' % (args.address + offset, format_value(value, args)))
    return True


def do_write(client: ModbusClient, args: argparse.Namespace) -> bool:
    if args.command == 'write-coil':
        return client.write_single_coil(args.address, args.value)
    if args.command == 'write-coils':
        return client.write_multiple_coils(args.address, args.values)
    if args.command == 'write-register':
        return client.write_single_register(args.address, to_unsigned(args.value))
    return client.write_multiple_registers(args.address, [to_unsigned(v) for v in args.values])


def do_device_id(client: ModbusClient, args: argparse.Namespace) -> bool:
    response = client.read_device_identification(read_code=args.code, object_id=args.object_id)
    if response is None:
        return False
    for obj_id, value in sorted(response.objects_by_id.items()):
        # objects are bytes sent by the server: never print raw control chars on the terminal
        text = value.decode('ascii', errors='backslashreplace')
        text = ''.join(c if c.isprintable() else '\\x%02x' % ord(c) for c in text)
        print('0x%02X %s' % (obj_id, text))
    if response.more_follows:
        print('# more objects available: use -o 0x%02X' % response.next_object_id, file=sys.stderr)
    return True


def print_error(client: ModbusClient) -> None:
    msg = client.last_error_as_txt
    if client.last_except:
        msg += ': exception 0x%02X (%s)' % (client.last_except, client.last_except_as_txt)
    print('error: %s' % msg, file=sys.stderr)


def run_command(client: ModbusClient, args: argparse.Namespace) -> bool:
    """Run the command of args, return True on success (read commands with an interval loop until Ctrl-C)."""
    if args.command.startswith('read-'):
        while True:
            ok = do_read(client, args)
            if args.interval is None:
                return ok
            if not ok:
                print_error(client)
            time.sleep(args.interval)
    if args.command.startswith('write-'):
        return do_write(client, args)
    return do_device_id(client, args)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING, format='%(message)s')
    try:
        with ModbusClient(host=args.host, port=args.port, unit_id=args.unit_id, timeout=args.timeout) as client:
            ok = run_command(client, args)
    except (TypeError, ValueError) as e:
        # parameter refused by the library (unit id, address or number of items out of range...)
        parser.error(str(e))
    except KeyboardInterrupt:
        return 0
    if not ok:
        print_error(client)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
