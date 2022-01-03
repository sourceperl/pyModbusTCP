#!/usr/bin/env python3

# write_bit
# write 4 bits to True, wait 2s, write False, restart...

import time
from pyModbusTCP.client import ModbusClient

# init
c = ModbusClient(host='localhost', port=502, auto_open=True, debug=False)
toggle = True

# main loop
while True:
    # open or reconnect TCP to server
    if not c.is_open():
        if not c.open():
            print('unable to connect')

    # if open() is ok, write coils (modbus function 0x01)
    if c.is_open():
        # write 4 bits in modbus address 0 to 3
        print("")
        print("write bits")
        print("----------")
        print("")
        for addr in range(4):
            is_ok = c.write_single_coil(addr, toggle)
            if is_ok:
                print("bit #" + str(addr) + ": write to " + str(toggle))
            else:
                print("bit #" + str(addr) + ": unable to write " + str(toggle))
            time.sleep(0.5)

        time.sleep(1)

        print("")
        print("read bits")
        print("---------")
        print("")
        bits = c.read_coils(0, 4)
        if bits:
            print("bits #0 to 3: "+str(bits))
        else:
            print("unable to read")

    toggle = not toggle
    # sleep 2s before next polling
    time.sleep(2)
