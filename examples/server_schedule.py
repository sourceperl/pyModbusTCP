#!/usr/bin/env python3

"""
Modbus/TCP server with start/stop schedule
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The server runs from 06:00 to 18:00 (local time), whatever the time the
script is launched at.

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
import datetime
import logging
import time

from pyModbusTCP.server import ModbusServer

logger = logging.getLogger('server_schedule')

# daily service hours, local time: the server is stopped from 18:00 to 06:00
SERVICE_START = datetime.time(6, 0)
SERVICE_STOP = datetime.time(18, 0)
# update period of the life word, in seconds
ALIVE_WORD_PERIOD = 10.0


def in_service(now: datetime.time) -> bool:
    """Return True if the server must run at this time of the day."""
    return SERVICE_START <= now < SERVICE_STOP


def apply_schedule() -> None:
    """Start or stop the server for the current time.

    The state is computed from the clock, not from a start/stop event: right whatever the time the script is
    launched at (start() raises NetworkError if the server already runs), and a start that failed (port still in
    use...) is tried again at the next check (every second).
    """
    if in_service(datetime.datetime.now().time()):
        if not server.is_running:
            try:
                server.start()
                logger.info('server started')
            except ModbusServer.NetworkError as e:
                logger.error('unable to start the server (retry at the next check): %s', e)
    elif server.is_running:
        server.stop()
        logger.info('server stopped (out of service hours)')


def alive_word_job() -> None:
    """Update holding register @0 with the tens of seconds since 00:00 UTC."""
    server.store.app.holding_registers.write(0, [int(time.time()) % (24*3600) // 10])


# parse args
parser = argparse.ArgumentParser()
parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
args = parser.parse_args()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
# init modbus server (its data store is kept while it is stopped)
server = ModbusServer(host=args.host, port=args.port, no_block=True)
# main loop: check the schedule every second, update the life word at @0 every ALIVE_WORD_PERIOD seconds
next_alive_word = time.monotonic()
try:
    while True:
        apply_schedule()
        if time.monotonic() >= next_alive_word:
            alive_word_job()
            next_alive_word = time.monotonic() + ALIVE_WORD_PERIOD
        time.sleep(1)
finally:
    server.stop()
