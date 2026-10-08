#!/usr/bin/env python3

"""
Example script running a Modbus TCP server with dynamic port allocation and full debug message logging.
"""

import logging
import sys

from pyModbusTCP.server import ModbusServer

# logging setup
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)-7s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logging.getLogger('pyModbusTCP.server').setLevel(logging.DEBUG)
# init modbus server
server = ModbusServer(host='0.0.0.0', port=0, no_block=True)

try:
    # start the server and retrieve the full address structure
    addr_info = server.start()

    # safely unpack host and port, supporting both IPv4 and IPv6 structures
    host, port = addr_info[0], addr_info[1]
    logger.info(f"Modbus server started successfully at {host}:{port}")

    # block the main thread to keep the service running in the background
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
