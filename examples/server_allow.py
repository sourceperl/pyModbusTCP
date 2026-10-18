#!/usr/bin/env python3

"""
An example of Modbus/TCP server which allow modbus read and/or write only from
specific IPs, with two levels of rules:

- allowed_hosts (ModbusServer): who can connect. Another client is disconnected
  at once, before any request (whatever its function, device identification
  included);
- AccessControl (a wrapper of the data store): what a connected client can do.
  Here only some clients can write, a write of another one gets an "illegal
  function" exception.

The application itself (server.store.app) is never restricted.

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
$ ./server_allow.py --host 192.168.1.10 --port 5020
"""

import argparse

from pyModbusTCP.server import AccessControl, MemoryStore, ModbusServer

# the clients allowed to connect (and read), and the ones allowed to write (addresses or networks)
ALLOWED_HOSTS = ['127.0.0.1', '192.168.0.10']
WRITE_HOSTS = ['127.0.0.1']


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()

    # the default store, with an access control stacked on it: only some clients can write
    store = AccessControl(MemoryStore(), write_hosts=WRITE_HOSTS)

    # init modbus server and start it: only some clients can connect
    server = ModbusServer(host=args.host, port=args.port, store=store, allowed_hosts=ALLOWED_HOSTS)
    try:
        server.start()
    except KeyboardInterrupt:
        pass
