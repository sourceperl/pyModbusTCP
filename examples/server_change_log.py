#!/usr/bin/env python3

# An example of Modbus/TCP server with a change logger
#
# run this as root to listen on TCP priviliged ports (<= 1024) to avoid [Errno 13]

import argparse
import logging
from pyModbusTCP.server import ModbusServer, ModbusServerDataBank


class MyDataBank(ModbusServerDataBank):
    def on_coils_change(self, address, from_value, to_value, srv_infos):
        msg = 'change in coil space [{0!r:^5} > {1!r:^5}] at @ 0x{2:04X} from ip: {3:<15}'
        msg = msg.format(from_value, to_value, address, srv_infos.client_addr)
        logging.info(msg)

    def on_holding_registers_change(self, address, from_value, to_value, srv_infos):
        msg = 'change in hreg space [{0!r:^5} > {1!r:^5}] at @ 0x{2:04X} from ip: {3:<15}'
        msg = msg.format(from_value, to_value, address, srv_infos.client_addr)
        logging.info(msg)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port')
    args = parser.parse_args()
    # logging setup
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO)
    # init modbus server and start it
    server = ModbusServer(host=args.host, port=args.port, data_bank=MyDataBank())
    server.start()
