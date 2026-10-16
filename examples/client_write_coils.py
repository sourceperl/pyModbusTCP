#!/usr/bin/env python3

"""Write 4 coils to True, wait 2s, write False and redo it."""

import time

from pyModbusTCP.client import ModbusClient

# init
c = ModbusClient(host='localhost', port=502, auto_open=True)
bit = True

# main loop
while True:
    # write 4 bits in modbus address 0 to 3
    print('write bits')
    print('----------\n')
    for ad in range(4):
        is_ok = c.write_single_coil(ad, bit)
        if is_ok:
            print(f'coil #{ad}: write to {bit}')
        else:
            print(f'coil #{ad}: unable to write {bit}')
        time.sleep(0.5)

    print()
    time.sleep(1)

    # read 4 bits in modbus address 0 to 3
    print('read bits')
    print('---------\n')
    bits = c.read_coils(0, 4)
    if bits:
        print(f'coils #0 to 3: {bits}')
    else:
        print('coils #0 to 3: unable to read')

    # toggle
    bit = not bit
    # sleep 2s before next polling
    print()
    time.sleep(2)
