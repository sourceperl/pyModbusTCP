#!/usr/bin/env python3

"""
An example of Modbus/TCP server with a change logger.

A listener of the data store gets one event per request that changed some
values (after the request is committed): here, it logs every coil and holding
register changed by a client. threaded=True: the log runs in a thread of the
store, it never delays the response to the client.

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
$ ./server_change_log.py --host 192.168.1.10 --port 5020
"""

import argparse
import logging

from pyModbusTCP.server import ChangeEvent, MemoryStore, ModbusServer

logger = logging.getLogger(__name__)


def log_changes(event: ChangeEvent) -> None:
    """Called once per client request that changed some values."""
    assert event.ctx is not None
    for change in event.changes:
        logger.info('change in %-17s [%5r > %5r] at @ 0x%04X from ip: %-15s (function 0x%02X)',
                    change.space.value, change.old, change.new, change.address, event.ctx.client.address,
                    event.ctx.function_code)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()

    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)

    # init modbus server and start it
    store = MemoryStore()
    store.subscribe(log_changes, threaded=True)
    server = ModbusServer(host=args.host, port=args.port, store=store)
    try:
        server.start()
    except KeyboardInterrupt:
        pass
