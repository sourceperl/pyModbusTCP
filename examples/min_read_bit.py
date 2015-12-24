#!/usr/bin/env python
# -*- coding: utf-8 -*-
import time

# min_read_bit
# minimal code for read 10 bits on IPv4 192.168.0.200 and print result on stdout

from pyModbusTCP.client import ModbusClient
c = ModbusClient(host="192.168.0.200", auto_open=True)

while True:
    # read 10 bits at address 20480
    bits = c.read_coils(20480, 10)
    print("bit ad #0 to 9: "+str(bits) if bits else "read error")
    # sleep 2s
    time.sleep(2)
