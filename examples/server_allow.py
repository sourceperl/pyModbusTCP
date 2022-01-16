#!/usr/bin/env python3

"""
An example of Modbus/TCP server which allow modbus read and/or write only from
specific IPs.

Run this as root to listen on TCP privileged ports (<= 1024).
"""

import argparse
from pyModbusTCP.server import ModbusServer, ModbusServerDataHandler, DataHandlerReturn
from pyModbusTCP.constants import EXP_ILLEGAL_FUNCTION


# some const
ALLOW_R_L = ['127.0.0.1', '192.168.0.10']
ALLOW_W_L = ['127.0.0.1']


# a custom data handler with IPs filter
class MyDataHandler(ModbusServerDataHandler):
    def read_coils(self, address, count, srv_info):
        if srv_info.client.address in ALLOW_R_L:
            return super().read_coils(address, count, srv_info)
        else:
            return DataHandlerReturn(exp_code=EXP_ILLEGAL_FUNCTION)

    def read_d_inputs(self, address, count, srv_info):
        if srv_info.client.address in ALLOW_R_L:
            return super().read_d_inputs(address, count, srv_info)
        else:
            return DataHandlerReturn(exp_code=EXP_ILLEGAL_FUNCTION)

    def read_h_regs(self, address, count, srv_info):
        if srv_info.client.address in ALLOW_R_L:
            return super().read_h_regs(address, count, srv_info)
        else:
            return DataHandlerReturn(exp_code=EXP_ILLEGAL_FUNCTION)

    def read_i_regs(self, address, count, srv_info):
        if srv_info.client.address in ALLOW_R_L:
            return super().read_i_regs(address, count, srv_info)
        else:
            return DataHandlerReturn(exp_code=EXP_ILLEGAL_FUNCTION)

    def write_coils(self, address, bits_l, srv_info):
        if srv_info.client.address in ALLOW_W_L:
            return super().write_coils(address, bits_l, srv_info)
        else:
            return DataHandlerReturn(exp_code=EXP_ILLEGAL_FUNCTION)

    def write_h_regs(self, address, words_l, srv_info):
        if srv_info.client.address in ALLOW_W_L:
            return super().write_h_regs(address, words_l, srv_info)
        else:
            return DataHandlerReturn(exp_code=EXP_ILLEGAL_FUNCTION)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()
    # init modbus server and start it
    server = ModbusServer(host=args.host, port=args.port, data_hdl=MyDataHandler())
    server.start()
