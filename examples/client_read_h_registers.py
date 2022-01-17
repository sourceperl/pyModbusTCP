#!/usr/bin/env python3

""" Read 10 holding registers and print result on stdout. """

import time
from pyModbusTCP.client import ModbusClient


# init modbus client
c = ModbusClient(debug=False, auto_open=True)

# main read loop
while True:
    # read 10 registers at address 0, store result in regs list
    regs_l = c.read_holding_registers(0, 10)

    # if success display registers
    if regs_l:
        print('reg ad #0 to 9: %s' % regs_l)
    else:
        print('unable to read registers')

    # sleep 2s before next polling
    time.sleep(2)
