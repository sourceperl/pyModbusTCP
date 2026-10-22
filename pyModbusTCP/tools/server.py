"""
mbtcp-server: a Modbus/TCP server
=================================

Run it as "mbtcp-server" (pyModbusTCP-cli package) or "python -m pyModbusTCP.tools server".

A Modbus/TCP server with the default data store of pyModbusTCP (65536 coils, discrete inputs, holding registers
and input registers, all set to 0 at startup). Useful to test a Modbus client, a SCADA, a gateway...

SECURITY
--------
Modbus/TCP has no authentication: any client that can reach the server can read and write its data.
- The server listens on localhost by default. Use -H with the address of the network interface that needs it
  (or 0.0.0.0 for every IPv4 interface, only if every network of the host is trusted).
- -A/--allow (repeatable) restricts the clients to some IP addresses or networks.
- --read-only refuses every write request (functions 0x05, 0x06, 0x0F, 0x10, 0x15, 0x16 and 0x17).
- --max-connections-per-host (default 16) keeps a single client host from taking every connection of
  --max-connections (default 64). An IPv6 client host is a /64 network (--ipv6-host-prefix).
- The default port is 5020: port 502 is a privileged port on Unix systems. Never run this command as root (or
  with sudo) to listen on it, give the CAP_NET_BIND_SERVICE capability to the Python interpreter of a dedicated
  virtual environment instead (see the pyModbusTCP examples README).

USAGE
-----
$ mbtcp-server                                       # localhost, port 5020
$ mbtcp-server -H 192.168.1.10 -A 192.168.1.0/24 --read-only --log-changes
$ mbtcp-server -p 502                                # needs the CAP_NET_BIND_SERVICE capability
"""

import argparse
import logging
import signal
import sys
from types import FrameType

from ..server import ChangeEvent, MemoryStore, ModbusServer, Space
from ._common import (
    PORT_502_HELP,
    add_ipv6_host_prefix_arg,
    add_version_arg,
    ip_or_network,
    is_permission_error,
    positive_float,
    setup_logging,
    tcp_port,
)

logger = logging.getLogger('mbtcp-server')

DEFAULT_PORT = 5020


def log_changes(event: ChangeEvent) -> None:
    """Change listener: log every coil and holding register changed by a client request."""
    assert event.ctx is not None
    for change in event.changes:
        kind = 'Coil' if change.space is Space.COILS else 'Holding register'
        logger.info('%s @%d: %s -> %s (from %s)', kind, change.address, change.old, change.new,
                    event.ctx.client.address)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='mbtcp-server', description='Modbus/TCP server',
                                     epilog='Port 502 without root: ' + PORT_502_HELP)
    parser.add_argument('-H', '--host', default='localhost',
                        help='listen address (default: localhost), 0.0.0.0 for every IPv4 interface')
    parser.add_argument('-p', '--port', type=tcp_port, default=DEFAULT_PORT,
                        help=f'TCP port (default: {DEFAULT_PORT})')
    parser.add_argument('-6', '--ipv6', action='store_true', help='use IPv6 (like -6 -H ::1)')
    parser.add_argument('-A', '--allow', action='append', type=ip_or_network, metavar='IP_OR_NETWORK',
                        help='allowed client IP address or network (like 10.0.0.0/24), repeatable '
                             '(default: every client)')
    parser.add_argument('--read-only', action='store_true', help='refuse every write request')
    parser.add_argument('--max-connections', type=int, default=64,
                        help='max number of simultaneous clients (default: 64)')
    parser.add_argument('--max-connections-per-host', type=int, default=16,
                        help='max number of simultaneous connections of a client host (default: 16)')
    add_ipv6_host_prefix_arg(parser)
    parser.add_argument('--idle-timeout', type=positive_float, default=300.0,
                        help='close a client session without request after this delay in s (default: 300)')
    parser.add_argument('--log-changes', action='store_true',
                        help='log every change of a coil or a holding register done by a client')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode (log every session event)')
    add_version_arg(parser)
    return parser


def serve(server: ModbusServer, args: argparse.Namespace) -> int:
    """Run the server until Ctrl-C or SIGTERM, return the exit status."""

    # SIGTERM (systemd, docker stop, kill...): stop the server cleanly, like Ctrl-C
    def on_sigterm(_signum: int, _frame: FrameType | None) -> None:
        logger.info('SIGTERM received, shutting down')
        server.stop()

    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        # blocking call, returns when the server is stopped
        server.start()
    except ModbusServer.NetworkError as e:
        logger.critical('Unable to listen on %s port %d: %s', args.host, args.port, e)
        if is_permission_error(e) and args.port < 1024:
            logger.critical('Port %d is privileged: %s', args.port, PORT_502_HELP)
        return 2
    except KeyboardInterrupt:
        logger.info('Interrupted, shutting down')
        server.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.debug)
    if not args.debug:
        # the library logs every session event at debug level only
        logging.getLogger('pyModbusTCP').setLevel(logging.INFO)
    store = MemoryStore()
    if args.log_changes:
        # threaded: the log never delays the response of a client
        store.subscribe(log_changes, threaded=True)
    try:
        server = ModbusServer(host=args.host, port=args.port, ipv6=args.ipv6, store=store,
                              allowed_hosts=args.allow, read_only=args.read_only,
                              max_connections=args.max_connections,
                              max_connections_per_host=args.max_connections_per_host,
                              ipv6_host_prefix=args.ipv6_host_prefix,
                              idle_timeout=args.idle_timeout)
    except (TypeError, ValueError) as e:
        logger.critical('Invalid parameter: %s', e)
        return 2
    if args.allow:
        logger.info('Allowed clients: %s', ', '.join(server.allowed_hosts or []))
    if args.read_only:
        logger.info('Read-only mode: write requests are refused')
    return serve(server, args)


if __name__ == '__main__':
    sys.exit(main())
