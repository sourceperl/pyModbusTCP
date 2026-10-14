"""Entry points of the command line tools (console scripts of pyModbusTCP-tools and "python -m pyModbusTCP.tools").

A tool module is only imported when it is run: the gateways import pyserial, an optional dependency, so a missing
pyserial must only affect them, and with a clear message.
"""

import importlib
import sys
from typing import Callable, Dict, List, Optional

# command name -> module of pyModbusTCP.tools
COMMANDS: Dict[str, str] = {
    'server': 'server',
    'client': 'client',
    'tcp2rtu': 'tcp_to_rtu_gw',
    'rtu2tcp': 'rtu_to_tcp_gw',
}


def run_tool(module_name: str, argv: Optional[List[str]] = None) -> int:
    """Import a tool module and run its main(), return the exit status (2 if pyserial is missing)."""
    try:
        module = importlib.import_module('pyModbusTCP.tools.' + module_name)
    except ModuleNotFoundError as e:
        if e.name != 'serial':
            raise
        print('error: %s' % e, file=sys.stderr)
        return 2
    main: Callable[[Optional[List[str]]], int] = module.main
    return main(argv)


def run_server() -> int:
    """Console script mbtcp-server."""
    return run_tool('server')


def run_client() -> int:
    """Console script mbtcp-client."""
    return run_tool('client')


def run_tcp2rtu() -> int:
    """Console script mbtcp-tcp2rtu."""
    return run_tool('tcp_to_rtu_gw')


def run_rtu2tcp() -> int:
    """Console script mbtcp-rtu2tcp."""
    return run_tool('rtu_to_tcp_gw')


def main(argv: Optional[List[str]] = None) -> int:
    """python -m pyModbusTCP.tools COMMAND [ARGS...]"""
    args = sys.argv[1:] if argv is None else argv
    usage = 'usage: python -m pyModbusTCP.tools {%s} [-h] [ARGS...]' % ','.join(COMMANDS)
    if not args or args[0] in ('-h', '--help'):
        print(usage)
        print('\nRun a pyModbusTCP command line tool, "COMMAND --help" for its options.')
        return 0 if args else 2
    if args[0] not in COMMANDS:
        print(usage, file=sys.stderr)
        print('error: unknown command %r' % args[0], file=sys.stderr)
        return 2
    return run_tool(COMMANDS[args[0]], args[1:])
