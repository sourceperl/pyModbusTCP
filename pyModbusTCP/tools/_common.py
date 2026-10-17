"""Helpers shared by the command line tools."""

import argparse
import errno
import ipaddress
import logging
import math
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType

from ..constants import VERSION

# the serial gateways need pyserial, an optional dependency of pyModbusTCP
SERIAL_MISSING_MSG = 'this gateway needs pyserial: pip install "pyModbusTCP[serial]" (or pyModbusTCP-cli)'

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
    """argparse type: check that value is an IP address or a network (like 10.0.0.0/24)."""
    try:
        ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an IP address or a network') from None
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


def slave_address(value: str) -> int:
    """argparse type: the address of a Modbus/RTU slave (1 to 247: 0 is the broadcast, 248 to 255 are reserved)."""
    try:
        address = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value!r} is not an integer') from None
    if not 1 <= address <= 247:
        raise argparse.ArgumentTypeError('slave address must be between 1 and 247')
    return address


@contextmanager
def stop_on_sigterm(stop: Callable[[], None], log: logging.Logger) -> Iterator[None]:
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
