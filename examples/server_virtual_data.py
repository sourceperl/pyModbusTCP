#!/usr/bin/env python3

"""
Modbus/TCP server with virtual data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Map the system date and time to @ 0 to 5 on the "holding registers" space.
Only the reading of these registers in this address space is authorized. All
other requests return an illegal data address except.

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
$ ./server.py --host 192.168.1.10 --port 5020
"""

import argparse
from datetime import datetime

from pyModbusTCP.server import DataBank, ModbusServer


class MyDataBank(DataBank):
    """A custom ModbusServerDataBank for override get_holding_registers method."""

    def __init__(self) -> None:
        # turn off allocation of memory for standard modbus object types
        # only "holding registers" space will be replaced by dynamic build values.
        super().__init__(virtual_mode=True)

    def get_holding_registers(self, address: int, number: int = 1,
                              srv_info: ModbusServer.ServerInfo | None = None) -> list[int] | None:
        """Get virtual holding registers."""
        # populate virtual registers dict with current datetime values
        now = datetime.now()
        v_regs_d = {0: now.day, 1: now.month, 2: now.year,
                    3: now.hour, 4: now.minute, 5: now.second}
        # build a list of virtual regs to return to server data handler
        # return None if any of virtual registers is missing
        try:
            return [v_regs_d[a] for a in range(address, address + number)]
        except KeyError:
            return None


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()

    # init modbus server and start it
    server = ModbusServer(host=args.host, port=args.port, data_bank=MyDataBank())
    server.start()
