#!/usr/bin/env python3

"""
Modbus/TCP server with virtual data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Map the system date and time to @ 0 to 5 on the "holding registers" space.
Only the reading of these registers in this address space is authorized. All
other requests return an illegal data address exception.

The store has no memory (size 0): the only addresses are the mapped range,
computed at each request. A mapped range can also be added to a regular store,
next to the values kept in memory.

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
$ ./server_virtual_data.py --host 192.168.1.10 --port 5020
"""

import argparse
from datetime import datetime

from pyModbusTCP.server import MemoryStore, ModbusServer, RequestContext, Space


def date_and_time(address: int, count: int, ctx: RequestContext | None) -> list[int]:
    """Read the virtual registers 0 to 5: day, month, year, hour, minute, second."""
    now = datetime.now()
    values = [now.day, now.month, now.year, now.hour, now.minute, now.second]
    return values[address:address + count]


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()

    # a store without memory, with 6 read-only holding registers computed at each request
    store = MemoryStore(size=0)
    store.map(Space.HOLDING_REGISTERS, 0, 6, read=date_and_time)

    # init modbus server and start it
    server = ModbusServer(host=args.host, port=args.port, store=store)
    server.start()
