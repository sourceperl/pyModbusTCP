#!/usr/bin/env python3

"""
Modbus TCP server script with dynamic port allocation.

The OS picks a free TCP port (port=0), so this example never collides with another service of the host.
The chosen port is returned by server.start() (as a (host, port) tuple) and logged at startup.
"""

import logging
import sys
import time

from pyModbusTCP.server import ModbusServer

# Logging setup
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)-7s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
# Silence noisy third-party loggers
logging.getLogger('pyModbusTCP.server').setLevel(logging.CRITICAL)

# Server Configuration
# listen on localhost only: to accept remote clients, use the address of a network interface
# (or "0.0.0.0" for all IPv4 interfaces, "::" with ipv6=True for IPv6, only on trusted networks)
server = ModbusServer(host='localhost', port=0, no_block=True, ipv6=False)

try:
    host, port = server.start()
    logger.info('Modbus server listening on %s port %d', host, port)
    logger.info('Press Ctrl+C to stop the server')
    # Keep the main thread alive
    while server.is_running:
        # Some demo data (UTC time at @0), to have something to read from a client
        t = time.gmtime()
        server.store.app.holding_registers.write(0, [t.tm_hour, t.tm_min, t.tm_sec])
        server.wait(timeout=1.0)
except ModbusServer.NetworkError as e:
    # port already in use, invalid address, permission denied...
    logger.error('Unable to start the Modbus server: %s', e)
    sys.exit(1)
except KeyboardInterrupt:
    logger.info("Keyboard interrupt received. Shutting down server...")
finally:
    server.stop()
