#!/usr/bin/env python
# -*- coding: utf-8 -*-

# read_register
# read 10 registers and print result on stdout

from pyModbusTCP.client import ModbusClient
import time

SERVER_HOST = "localhost"
SERVER_PORT = 502

c = ModbusClient()

# uncomment this line to see debug message
# c.debug(True)

# define modbus server host, port
c.host(SERVER_HOST)
c.port(SERVER_PORT)

while True:
    # open or reconnect TCP to server
    if not c.is_open():
        if not c.open():
            print('unable to connect to %s:%s' % (SERVER_HOST, SERVER_PORT))

    # if open() is ok, read register (modbus function 0x03)
    if c.is_open():
        # read 10 registers at address 0, store result in regs list
        regs = c.read_holding_registers(0, 10)
        # if success display registers
        if regs:
            print("reg ad #0 to 9: "+str(regs))

    # sleep 2s before next polling
    time.sleep(2)
