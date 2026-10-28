"""Helpers shared by the command line tools."""

import argparse
import errno
import logging
import math
import signal
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from types import FrameType
from typing import TYPE_CHECKING

from ..constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    MASK_WRITE_REGISTER,
    MEI_TYPE_READ_DEVICE_ID,
    READ_COILS,
    READ_DISCRETE_INPUTS,
    READ_FUNCTION_CODES,
    READ_HOLDING_REGISTERS,
    READ_INPUT_REGISTERS,
    VERSION,
    WRITE_FILE_RECORD,
    WRITE_MULTIPLE_COILS,
    WRITE_MULTIPLE_REGISTERS,
    WRITE_READ_MULTIPLE_REGISTERS,
    WRITE_SINGLE_COIL,
    WRITE_SINGLE_REGISTER,
)
from ..server._common import HOST_IPV6_PREFIX
from ..server._validators import parse_network

if TYPE_CHECKING:
    # pyserial is optional: only imported by the serial tools
    from serial import Serial
    from serial.rs485 import RS485Settings

# the serial tools (the gateways, mbtcp-rtu-scan) need pyserial, an optional dependency of pyModbusTCP
SERIAL_MISSING_MSG = 'this tool needs pyserial: pip install "pyModbusTCP[serial]" (or pyModbusTCP-cli)'

# README section about the privileged port 502
PORT_502_HELP = ('see "Listen on the standard Modbus port 502 without root" in the pyModbusTCP examples README '
                 '(https://github.com/sourceperl/pyModbusTCP/tree/master/examples)')


def setup_logging(debug: bool) -> None:
    """Configure the root logger for a command line tool."""
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format='%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)-20s] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')


def version_text(prog: str) -> str:
    """Text printed by -v/--version: the name of the tool and the version of pyModbusTCP (where its code is)."""
    return f'{prog} (pyModbusTCP) {VERSION}'


def add_version_arg(parser: argparse.ArgumentParser) -> None:
    """Add the -v/--version option to a parser."""
    text = version_text(parser.prog)
    parser.add_argument('-v', '--version', action='version', version=text,
                        help="show the version of pyModbusTCP and exit")


def ip_or_network(value: str) -> str:
    """argparse type: check that value is an IP address or a network (like 10.0.0.0/24, not 10.0.0.5/24)."""
    try:
        parse_network(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    return value


def tcp_port(value: str) -> int:
    """argparse type: a TCP port number (1 to 65535)."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not a port number') from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('port must be between 1 and 65535')
    return port


# upper bound of the delays given on the command line (timeouts, intervals): one day, far above any useful value, but
# never "inf", "nan" or a number too large for time.sleep() and the socket/serial timeouts (OverflowError)
MAX_DELAY = 86400.0


def positive_float(value: str) -> float:
    """argparse type: a number of seconds greater than 0 (at most MAX_DELAY)."""
    try:
        f_value = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not a number') from None
    # "not >": also refuses nan
    if not f_value > 0 or not math.isfinite(f_value) or f_value > MAX_DELAY:
        raise argparse.ArgumentTypeError(f'value must be greater than 0 and at most {MAX_DELAY:.0f} s')
    return f_value


def positive_int(value: str) -> int:
    """argparse type: an integer greater than 0 (like a baud rate)."""
    try:
        i_value = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an integer') from None
    if i_value < 1:
        raise argparse.ArgumentTypeError('value must be greater than 0')
    return i_value


def unit_id(value: str) -> int:
    """argparse type: a Modbus unit id, 0 to 255, in any Python notation (1, 0x01)."""
    try:
        i_value = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an integer') from None
    if not 0 <= i_value <= 255:
        raise argparse.ArgumentTypeError('unit id must be between 0 and 255')
    return i_value


def ipv6_host_prefix(value: str) -> int:
    """argparse type: the prefix length of the IPv6 network counted as one client host (1 to 128)."""
    try:
        prefix = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an integer') from None
    if not 1 <= prefix <= 128:
        raise argparse.ArgumentTypeError('prefix length must be between 1 and 128')
    return prefix


def add_ipv6_host_prefix_arg(parser: argparse.ArgumentParser) -> None:
    """Add the --ipv6-host-prefix option (the IPv6 clients counted as one host by --max-connections-per-host)."""
    parser.add_argument('--ipv6-host-prefix', type=ipv6_host_prefix, default=HOST_IPV6_PREFIX, metavar='BITS',
                        help='IPv6 clients of the same network of this prefix length are one host for '
                             f'--max-connections-per-host (default: {HOST_IPV6_PREFIX}); 128 counts each address '
                             'apart: only if a host can\'t choose its addresses (no SLAAC, filtered sources)')


# upper bound of the RS-485 delays around a transmission: the Linux kernel keeps them in ms and caps them at 100 ms
MAX_RS485_DELAY = 0.1


def rs485_delay(value: str) -> float:
    """argparse type: a RS-485 delay in s, 0 to MAX_RS485_DELAY (the driver uses a resolution of 1 ms)."""
    try:
        f_value = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not a number') from None
    # "not <=": also refuses nan
    if not 0.0 <= f_value <= MAX_RS485_DELAY:
        raise argparse.ArgumentTypeError(f'value must be between 0 and {MAX_RS485_DELAY:g} s')
    return f_value


def add_rs485_args(parser: argparse.ArgumentParser) -> None:
    """Add the options of the RS-485 mode of the serial driver (the kernel drives RTS to switch the transceiver)."""
    group = parser.add_argument_group(
        'RS-485 mode', 'for a serial port whose RS-485 transceiver is switched by RTS (an UART of an embedded board); '
                       'useless with an USB-RS485 adapter, it switches by itself. The RTS levels are logical: '
                       '"high" is RTS asserted, a low voltage on the nRTS pin of most UARTs')
    group.add_argument('--rs485', action='store_true',
                       help='enable the RS-485 mode of the serial driver (Linux; on Windows, default levels only)')
    group.add_argument('--rs485-rts-tx', choices=('high', 'low'), default=None,
                       help='RTS level while sending (default: the level of the driver if its RS-485 mode is already '
                            'set, like by the device tree, else high)')
    group.add_argument('--rs485-rts-rx', choices=('high', 'low'), default=None,
                       help='RTS level while receiving (default: the level of the driver if its RS-485 mode is '
                            'already set, else low)')
    group.add_argument('--rs485-delay-before-tx', type=rs485_delay, default=None, metavar='S',
                       help=f'delay between RTS switch and the first byte sent, in s (0 to {MAX_RS485_DELAY:g}, '
                            'default: driver value)')
    group.add_argument('--rs485-delay-before-rx', type=rs485_delay, default=None, metavar='S',
                       help=f'delay between the last byte sent and RTS switch back, in s (0 to {MAX_RS485_DELAY:g}, '
                            'default: driver value)')


def check_listen_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse an IPv6 listen address without -6: the IPv4 socket can't bind it (a cryptic "address family" error)."""
    if ':' in args.host and not args.ipv6:
        parser.error(f'-H {args.host} is an IPv6 address: add -6')


def check_rs485_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Refuse a --rs485-* setting without --rs485 (it would be silently ignored)."""
    if args.rs485:
        return
    for name in ('rs485_rts_tx', 'rs485_rts_rx', 'rs485_delay_before_tx', 'rs485_delay_before_rx'):
        if getattr(args, name) is not None:
            parser.error(f'--{name.replace("_", "-")} needs --rs485')


def driver_rts_levels(port: 'Serial') -> tuple[bool, bool] | None:
    """The RTS levels (tx, rx) of the RS-485 mode already set in the Linux driver of an open port (like by the device
    tree), None if this mode is off, unknown (not Linux) or unsupported by the driver.
    """
    if not sys.platform.startswith('linux'):
        return None
    import array
    import fcntl

    from serial import serialposix
    # struct serial_rs485: flags, delay_rts_before_send, delay_rts_after_send, padding (the buffer of pyserial)
    buf = array.array('i', [0] * 8)
    try:
        fcntl.ioctl(port.fileno(), serialposix.TIOCGRS485, buf)
    except (OSError, AttributeError, ValueError):
        # no RS-485 support in the driver (a pseudo-terminal, an USB adapter...), or not a real port
        return None
    flags = buf[0]
    if not flags & serialposix.SER_RS485_ENABLED:
        # the flags of a disabled mode are meaningless (often all 0: RTS never asserted)
        return None
    return bool(flags & serialposix.SER_RS485_RTS_ON_SEND), bool(flags & serialposix.SER_RS485_RTS_AFTER_SEND)


@dataclass
class RS485Setup:
    """The RS-485 settings for pyserial and where the RTS levels come from (for the log)."""
    settings: 'RS485Settings'
    tx_from_driver: bool = False
    rx_from_driver: bool = False

    def text(self) -> str:
        """The settings for the log: "RTS low on tx (driver), high on rx (driver), delay before tx driver..."."""
        st = self.settings

        def level(high: bool, from_driver: bool) -> str:
            return ('high' if high else 'low') + (' (driver)' if from_driver else '')

        def delay(value: float | None) -> str:
            return 'driver' if value is None else f'{value:.3f} s'

        return (f'RTS {level(st.rts_level_for_tx, self.tx_from_driver)} on tx, '
                f'{level(st.rts_level_for_rx, self.rx_from_driver)} on rx, '
                f'delay before tx {delay(st.delay_before_tx)}, before rx {delay(st.delay_before_rx)}')


def rs485_settings(args: argparse.Namespace, driver_levels: tuple[bool, bool] | None = None) -> RS485Setup | None:
    """The RS-485 settings from the command line options, None without --rs485.

    A RTS level not given on the command line is the one of the driver (driver_levels, see driver_rts_levels()):
    pyserial always writes both levels, the configuration of the device tree would be lost. Without it, the levels of
    pyserial and Windows: RTS high to send, low to receive.
    """
    if not args.rs485:
        return None
    from serial.rs485 import RS485Settings

    def resolve(option: str | None, index: int, default: bool) -> tuple[bool, bool]:
        """The level (True: high) and True if it comes from the driver."""
        if option is not None:
            return option == 'high', False
        if driver_levels is not None:
            return driver_levels[index], True
        return default, False

    tx, tx_from_driver = resolve(args.rs485_rts_tx, 0, True)
    rx, rx_from_driver = resolve(args.rs485_rts_rx, 1, False)
    settings = RS485Settings(rts_level_for_tx=tx, rts_level_for_rx=rx,
                             delay_before_tx=args.rs485_delay_before_tx,
                             delay_before_rx=args.rs485_delay_before_rx)
    return RS485Setup(settings, tx_from_driver, rx_from_driver)


def apply_rs485(port: 'Serial', settings: 'RS485Settings') -> None:
    """Set the RS-485 mode of an open serial port, kept by pyserial when the port is reopened.

    :raises ValueError: the platform or the serial driver refuses it (like a pseudo-terminal or an USB adapter)
    """
    try:
        port.rs485_mode = settings
    except NotImplementedError as e:
        # pyserial on macOS and the BSDs
        raise ValueError(f'RS-485 mode not supported on this platform ({e})') from None
    except ValueError as e:
        raise ValueError(f'RS-485 mode refused by the serial driver of {port.port}: {e}') from None


def setup_rs485(port: 'Serial', args: argparse.Namespace) -> RS485Setup | None:
    """Apply the RS-485 options of the command line to an open serial port (nothing without --rs485).

    :raises ValueError: the platform or the serial driver refuses it
    """
    if not args.rs485:
        return None
    setup = rs485_settings(args, driver_rts_levels(port))
    assert setup is not None
    apply_rs485(port, setup.settings)
    return setup


# slave address 0 is the broadcast address of a serial line, 248 to 255 are reserved
BROADCAST_ADDRESS = 0
MAX_SLAVE_ADDRESS = 247


def slave_address(value: str) -> int:
    """argparse type: the address of a Modbus/RTU slave (1 to 247: 0 is the broadcast, 248 to 255 are reserved)."""
    try:
        address = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an integer') from None
    if not 1 <= address <= MAX_SLAVE_ADDRESS:
        raise argparse.ArgumentTypeError(f'slave address must be between 1 and {MAX_SLAVE_ADDRESS}')
    return address


@contextmanager
def stop_on_sigterm(stop: Callable[[], None], log: logging.Logger) -> Generator[None, None, None]:
    """In this block, SIGTERM (systemd, docker stop, kill...) calls stop(): a clean shutdown, like Ctrl-C.

    The previous handler is restored at the end. Only the main thread can set a signal handler: elsewhere (a tool run
    from a thread, in a test for example), nothing is changed.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def on_sigterm(_signum: int, _frame: FrameType | None) -> None:
        log.info('SIGTERM received, shutting down')
        stop()

    previous = signal.signal(signal.SIGTERM, on_sigterm)
    try:
        yield
    finally:
        # None: a handler not set from Python, it can't be restored (keep the default action)
        signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)


def is_permission_error(exc: BaseException) -> bool:
    """Return True if exc (or its cause) is an "access denied" error, like a bind on a privileged port."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, PermissionError) or getattr(current, 'errno', None) == errno.EACCES:
            return True
        current = current.__cause__
    return False


# --- the functions relayed by the gateways ---

# the functions that can be broadcast (unit id or slave address 0): writes only, no slave answers (a read has no
# meaning)
BROADCAST_FUNCTION_CODES = frozenset({WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, WRITE_MULTIPLE_COILS,
                                      WRITE_MULTIPLE_REGISTERS, WRITE_FILE_RECORD, MASK_WRITE_REGISTER})


@dataclass(frozen=True)
class FunctionFilter:
    """The functions that a gateway relays (a whitelist: an unknown function may write), --functions and --read-only of
    mbtcp-tcp2rtu (to the serial line) and mbtcp-rtu2tcp (to the Modbus/TCP server).

    0x2B (encapsulated interface transport) is a family of functions: it is allowed by MEI type (0x0E: read device
    identification, the other types, like CANopen 0x0D, can write).

    :param codes: the allowed function codes (0x01 to 0x7F, 0x2B excluded), None for every function
    :param mei_types: the allowed MEI types of the function 0x2B (ignored when codes is None)
    """

    codes: frozenset[int] | None
    mei_types: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if self.codes is not None:
            for code in self.codes:
                if not 0x01 <= code <= 0x7F or code == ENCAPSULATED_INTERFACE_TRANSPORT:
                    raise ValueError(f'invalid function code 0x{code:02X} in a function filter')
        for mei_type in self.mei_types:
            if not 0x00 <= mei_type <= 0xFF:
                raise ValueError(f'invalid MEI type {mei_type} in a function filter')

    @classmethod
    def parse(cls, text: str) -> 'FunctionFilter':
        """A filter from its text: "all", or function codes separated by commas, in decimal or hexadecimal, a
        function 0x2B with its MEI type: "1,2,3,4,0x06,0x10,0x2B/0x0E" (0x2B alone is 0x2B/0x0E).

        :raises ValueError: with a message for the user
        """
        if text.strip().lower() == 'all':
            return cls(None)
        codes: set[int] = set()
        mei_types: set[int] = set()
        for item in text.split(','):
            item = item.strip()
            if not item:
                raise ValueError(f'empty function code in {text!r}')
            code_txt, _, mei_txt = item.partition('/')
            try:
                code = int(code_txt, 0)
                mei_type = int(mei_txt, 0) if mei_txt else MEI_TYPE_READ_DEVICE_ID
            except ValueError:
                raise ValueError(f'{item!r} is not a function code (like 3, 0x10 or 0x2B/0x0E)') from None
            if not 0x01 <= code <= 0x7F:
                raise ValueError(f'function code {item!r} out of 1 to 127 (0x01 to 0x7F)')
            if code == ENCAPSULATED_INTERFACE_TRANSPORT:
                if not 0x00 <= mei_type <= 0xFF:
                    raise ValueError(f'MEI type of {item!r} out of 0 to 255')
                mei_types.add(mei_type)
            elif mei_txt:
                raise ValueError(f'{item!r}: only the function 0x2B has a MEI type')
            else:
                codes.add(code)
        return cls(frozenset(codes), frozenset(mei_types))

    def allows(self, pdu: bytes) -> bool:
        """True if the request PDU is of an allowed function."""
        if self.codes is None:
            return True
        if pdu[0] == ENCAPSULATED_INTERFACE_TRANSPORT:
            return len(pdu) >= 2 and pdu[1] in self.mei_types
        return pdu[0] in self.codes

    def text(self) -> str:
        """The filter for the log: "0x01, 0x02, 0x03, 0x2B/0x0E" or "every function"."""
        if self.codes is None:
            return 'every function'
        items = [f'0x{code:02X}' for code in sorted(self.codes)]
        items += [f'0x{ENCAPSULATED_INTERFACE_TRANSPORT:02X}/0x{mei:02X}' for mei in sorted(self.mei_types)]
        return ', '.join(items) or 'no function'


# the functions relayed by default: the standard functions that read and write data (bits and registers), and the
# read device identification; not the diagnostics (0x08 can restart a slave or make it silent), the file records,
# the other MEI types or the functions of a vendor (programming, firmware...)
DEFAULT_FUNCTIONS = FunctionFilter(frozenset({
    READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS,
    WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS,
    MASK_WRITE_REGISTER, WRITE_READ_MULTIPLE_REGISTERS}), frozenset({MEI_TYPE_READ_DEVICE_ID}))
# the functions relayed in read-only mode (--read-only, a shortcut for this filter, broadcast refused)
# (READ_FUNCTION_CODES, the reads of a read-only ModbusServer: its 0x2B is the read device identification only)
READ_ONLY_FUNCTIONS = FunctionFilter(frozenset(READ_FUNCTION_CODES) - {ENCAPSULATED_INTERFACE_TRANSPORT},
                                     frozenset({MEI_TYPE_READ_DEVICE_ID}))


def function_filter(value: str) -> FunctionFilter:
    """argparse type: the relayed functions ("all", or codes like "1,2,3,4,6,16,0x2B/0x0E")."""
    try:
        return FunctionFilter.parse(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
