#!/usr/bin/env python3

"""
An example of Modbus/TCP server which allow modbus read and/or write only from
specific IPs.

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

from pyModbusTCP.constants import EXP_ILLEGAL_FUNCTION
from pyModbusTCP.server import DataHandler, ModbusServer

# some const
ALLOW_R_L: list[str] = ['127.0.0.1', '192.168.0.10']
ALLOW_W_L: list[str] = ['127.0.0.1']


# a custom data handler with IPs filter
class MyDataHandler(DataHandler):
    def read_coils(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        if srv_info.client.address in ALLOW_R_L:
            return super().read_coils(address, count, srv_info)
        else:
            return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)

    def read_d_inputs(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        if srv_info.client.address in ALLOW_R_L:
            return super().read_d_inputs(address, count, srv_info)
        else:
            return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)

    def read_h_regs(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        if srv_info.client.address in ALLOW_R_L:
            return super().read_h_regs(address, count, srv_info)
        else:
            return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)

    def read_i_regs(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        if srv_info.client.address in ALLOW_R_L:
            return super().read_i_regs(address, count, srv_info)
        else:
            return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)

    def write_coils(self, address: int, bits_l: list[bool], srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        if srv_info.client.address in ALLOW_W_L:
            return super().write_coils(address, bits_l, srv_info)
        else:
            return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)

    def write_h_regs(self, address: int, words_l: list[int], srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        if srv_info.client.address in ALLOW_W_L:
            return super().write_h_regs(address, words_l, srv_info)
        else:
            return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()

    # init modbus server and start it
    server: ModbusServer = ModbusServer(host=args.host, port=args.port, data_hdl=MyDataHandler())
    try:
        server.start()
    except KeyboardInterrupt:
        pass
