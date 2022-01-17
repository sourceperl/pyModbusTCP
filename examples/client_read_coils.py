#!/usr/bin/env python3

""" Read 10 coils and print result on stdout. """

import time
from pyModbusTCP.client import ModbusClient

# init modbus client
c = ModbusClient(host='localhost', port=502, auto_open=True, debug=False)

# main read loop
while True:
    # read 10 bits (= coils) at address 0, store result in coils list
    coils_l = c.read_coils(0, 10)

    # if success display registers
    if coils_l:
        print('coil ad #0 to 9: %s' % coils_l)
    else:
        print('unable to read coils')

    # sleep 2s before next polling
    time.sleep(2)
