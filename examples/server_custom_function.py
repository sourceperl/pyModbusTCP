#!/usr/bin/env python3

"""
Modbus/TCP server with a custom function
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The server routes each request to the handler of its function code
(server.functions). A handler takes the request and returns the response PDU,
or raises a Modbus exception. Here:

- a new function 0x41 (in the user defined range 0x41 to 0x48) returns the
  uptime of the server in seconds and the number of requests served, as two
  32 bits unsigned ints;
- the standard function "write single register" (0x06) is replaced to refuse
  a setpoint out of 0 to 100 (illegal data value) and keeps the standard
  behavior otherwise;
- a fallback handler logs the requests of unsupported functions.

Test it with a client:
$ ./server_custom_function.py --port 5020
$ python3 -c "from pyModbusTCP.client import ModbusClient; print(ModbusClient(port=5020).custom_request(b'\\x41'))"

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
"""

import argparse
import logging
import time

from pyModbusTCP.server import (
    STANDARD_FUNCTIONS,
    IllegalDataValue,
    IllegalFunction,
    ModbusServer,
    Request,
)

START = time.monotonic()
served = 0

logger = logging.getLogger(__name__)


def server_status(req: Request) -> bytes:
    """Function 0x41: uptime (s) and requests served, 2 x uint32."""
    global served
    served += 1
    return req.reply('II', int(time.monotonic() - START), served)


def write_setpoint(req: Request) -> bytes:
    """Function 0x06 with a limit: the registers are setpoints from 0 to 100."""
    (_address, value) = req.unpack('>HH', 1, 5)
    if value > 100:
        raise IllegalDataValue(f'setpoint {value} out of 0 to 100')
    return STANDARD_FUNCTIONS[0x06](req)


def unsupported(req: Request) -> bytes:
    """Fallback: the function codes without handler."""
    logger.warning('unsupported function 0x%02X from %s', req.function_code, req.ctx.client.address)
    raise IllegalFunction()


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    server = ModbusServer(host=args.host, port=args.port, fallback=unsupported)
    # register a new function, replace a standard one (server.function() can also be used as a decorator)
    server.function(0x41)(server_status)
    server.function(0x06)(write_setpoint)
    try:
        server.start()
    except KeyboardInterrupt:
        pass
