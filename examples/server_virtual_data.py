#!/usr/bin/env python3

# An example of Modbus/TCP server with virtual data
#
# Map the system date and time to @ 0 to 5 on the "holding registers" space. Only the reading
# of these registers in this address space is authorized. All other requests return an illegal
# data address except.
#
# run this as root to listen on TCP priviliged ports (<= 1024) to avoid [Errno 13]

import argparse
from pyModbusTCP.server import ModbusServer, ModbusServerDataBank
from datetime import datetime


class MyDataBank(ModbusServerDataBank):
    def __init__(self):
        # turn off allocation of memory for standard modbus object types
        # only "holding registers" space will be replace by dynamic build values
        conf = ModbusServerDataBank.Conf(virtual_mode=True)
        super().__init__(conf=conf)

    def get_holding_registers(self, address, number=1):
        # populate virtual registers dict with current datetime values
        now = datetime.now()
        v_regs_d = {0: now.day, 1: now.month, 2: now.year,
                    3: now.hour, 4: now.minute, 5: now.second}
        # build a list of virtual regs to return to server data handler
        # return None if any of virtual registers is missing
        try:
            return [v_regs_d[a] for a in range(address, address+number)]
        except KeyError:
            return None


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port')
    args = parser.parse_args()
    # init modbus server and start it
    server = ModbusServer(host=args.host, port=args.port, data_bank=MyDataBank())
    server.start()

