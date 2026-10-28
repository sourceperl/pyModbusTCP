#!/usr/bin/env python3

"""
Modbus/TCP server with a device identification
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Answer the "Read Device Identification" function (0x2B/0x0E): the server
publishes some objects (bytes), read by the clients by category:

- basic (read code 1): VendorName, ProductCode, MajorMinorRevision (0x00 to 0x02)
- regular (read code 2): VendorUrl, ProductName, ModelName, UserApplicationName
  (0x03 to 0x06, up to 0x7F)
- extended (read code 3): private objects of the vendor (0x80 to 0xFF)
- one object (read code 4): any object, by its id

Every client can read these objects: never publish anything sensitive.

Run the server, then read its identification with the client of pyModbusTCP:
$ ./server_device_id.py --port 5020
$ python -m pyModbusTCP.tools client -p 5020 device-id            # basic
$ python -m pyModbusTCP.tools client -p 5020 device-id -c 2       # regular
$ python -m pyModbusTCP.tools client -p 5020 device-id -c 3       # extended
$ python -m pyModbusTCP.tools client -p 5020 device-id -c 4 -o 0x81

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
$ ./server_device_id.py --host 192.168.1.10 --port 5020
"""

import argparse
import logging
from datetime import datetime

from pyModbusTCP.server import DeviceIdentification, ModbusServer

logger = logging.getLogger('server_device_id')


def build_device_id() -> DeviceIdentification:
    """The objects of the device identification (always bytes, at most 244 bytes each)."""
    return DeviceIdentification(
        # basic objects (mandatory in the specification)
        vendor_name=b'MyCompany',
        product_code=b'PLC-001',
        major_minor_revision=b'1.2',
        # regular objects (optional)
        vendor_url=b'https://www.example.com',
        product_name=b'Super PLC',
        model_name=b'X1',
        user_application_name=b'Firmware V04.10.44',
        # extended (private) objects: a dict {object id: value}, ids 0x80 to 0xFF
        objects_by_id={
            0x80: b'foo',
            0x81: f'started at {datetime.now():%Y-%m-%d %H:%M:%S}'.encode(),
        },
    )


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser(description='Modbus/TCP server with a device identification')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    parser.add_argument('-d', '--debug', action='store_true', help='set debug mode')
    args = parser.parse_args()
    # logging setup
    logging.basicConfig(level=logging.INFO)
    if args.debug:
        logging.getLogger('pyModbusTCP.server').setLevel(logging.DEBUG)

    # init modbus server and start it
    device_id = build_device_id()
    server = ModbusServer(host=args.host, port=args.port, device_id=device_id)
    # the objects can also be changed while the server runs (from another thread, DeviceIdentification is
    # thread-safe), like: device_id.user_application_name = b'new name' or device_id[0x82] = b'value'
    try:
        server.start()
    except KeyboardInterrupt:
        pass
