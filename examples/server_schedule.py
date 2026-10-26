#!/usr/bin/env python3

"""
Modbus/TCP server with start/stop schedule
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
$ ./server_schedule.py --host localhost --port 5020
"""

import argparse
import time

# pip install schedule==1.2.2
import schedule

from pyModbusTCP.server import ModbusServer


def alive_word_job() -> None:
    """Update holding register @0 with the tens of seconds since 00:00 UTC."""
    server.store.app.holding_registers.write(0, [int(time.time()) % (24*3600) // 10])


# parse args
parser = argparse.ArgumentParser()
parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
args = parser.parse_args()
# init modbus server and start it
server = ModbusServer(host=args.host, port=args.port, no_block=True)
server.start()
# init scheduler
# schedule a daily downtime (from 18:00 to 06:00)
schedule.every().day.at('18:00').do(server.stop)
schedule.every().day.at('06:00').do(server.start)
# update life word at @0
schedule.every(10).seconds.do(alive_word_job)
# main loop
while True:
    schedule.run_pending()
    time.sleep(1)
