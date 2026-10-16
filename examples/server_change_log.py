#!/usr/bin/env python3

"""
An example of Modbus/TCP server with a change logger.

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
import logging

from pyModbusTCP.server import DataBank, ModbusServer

logger = logging.getLogger(__name__)


class MyDataBank(DataBank):
    """A custom ModbusServerDataBank for override on_xxx_change methods."""

    def on_coils_change(self, address: int, from_value: bool, to_value: bool,
                        srv_info: ModbusServer.ServerInfo) -> None:
        """Call by server when change occur on coils space."""
        msg = 'change in coil space [{0!r:^5} > {1!r:^5}] at @ 0x{2:04X} from ip: {3:<15}'
        msg = msg.format(from_value, to_value, address, srv_info.client.address)
        logger.info(msg)

    def on_holding_registers_change(self, address: int, from_value: int, to_value: int,
                                    srv_info: ModbusServer.ServerInfo) -> None:
        """Call by server when change occur on holding registers space[cite: 3]."""
        msg = 'change in hreg space [{0!r:^5} > {1!r:^5}] at @ 0x{2:04X} from ip: {3:<15}'
        msg = msg.format(from_value, to_value, address, srv_info.client.address)
        logger.info(msg)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()

    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)

    # init modbus server and start it
    server: ModbusServer = ModbusServer(host=args.host, port=args.port, data_bank=MyDataBank())
    try:
        server.start()
    except KeyboardInterrupt:
        pass
