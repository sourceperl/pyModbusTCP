#!/usr/bin/env python3

"""
Modbus TCP server script with dynamic port allocation.
"""

import logging
import sys

from pyModbusTCP.server import ModbusServer

# Logging setup
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)-7s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
# Silence noisy third-party loggers
logging.getLogger('pyModbusTCP.server').setLevel(logging.CRITICAL)

# Server Configuration
# For IPv4: host="0.0.0.0", use_ipv6=False
# For IPv6 / Dual-stack: host="::", use_ipv6=True
server = ModbusServer(host='0.0.0.0', port=0, no_block=True, ipv6=False)

try:
    # Start the server
    server.start()
    assert server.tcp_server is not None
    bound_host = server.tcp_server.server_address[0]
    bound_port = server.tcp_server.server_address[1]
    logger.info(f'Server started successfully on "{bound_host}" at TCP port {bound_port}')

    # Keep the main thread alive in non-blocking mode
    server.wait()
except KeyboardInterrupt:
    logger.info("Keyboard interrupt received. Shutting down server...")
except Exception as e:
    logger.error(f"An unexpected error occurred: {e}")
    sys.exit(1)
finally:
    # Ensure proper cleanup on exit
    server.stop()
    logger.info("Modbus server stopped.")
