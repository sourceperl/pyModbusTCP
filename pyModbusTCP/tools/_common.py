"""Helpers shared by the command line tools."""

import argparse
import errno
import ipaddress
import logging
from typing import Optional

from ..constants import VERSION

# the serial gateways need pyserial, an optional dependency of pyModbusTCP
SERIAL_MISSING_MSG = 'this gateway needs pyserial: pip install "pyModbusTCP[serial]" (or pyModbusTCP-tools)'

# README section about the privileged port 502
PORT_502_HELP = ('see "Listen on the standard Modbus port 502 without root" in the pyModbusTCP examples README '
                 '(https://github.com/sourceperl/pyModbusTCP/tree/master/examples)')


def setup_logging(debug: bool) -> None:
    """Configure the root logger for a command line tool."""
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format='%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)-20s] %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')


def add_version_arg(parser: argparse.ArgumentParser) -> None:
    """Add the -V/--version option to a parser."""
    parser.add_argument('-V', '--version', action='version', version="%(prog)s " + VERSION)


def ip_or_network(value: str) -> str:
    """argparse type: check that value is an IP address or a network (like 10.0.0.0/24)."""
    try:
        ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        raise argparse.ArgumentTypeError('%r is not an IP address or a network' % value) from None
    return value


def tcp_port(value: str) -> int:
    """argparse type: a TCP port number (1 to 65535)."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('%r is not a port number' % value) from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('port must be between 1 and 65535')
    return port


def positive_float(value: str) -> float:
    """argparse type: a number of seconds greater than 0."""
    try:
        f_value = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('%r is not a number' % value) from None
    if not f_value > 0:
        raise argparse.ArgumentTypeError('value must be greater than 0')
    return f_value


def is_permission_error(exc: BaseException) -> bool:
    """Return True if exc (or its cause) is an "access denied" error, like a bind on a privileged port."""
    current: Optional[BaseException] = exc
    while current is not None:
        if isinstance(current, PermissionError) or getattr(current, 'errno', None) == errno.EACCES:
            return True
        current = current.__cause__
    return False
