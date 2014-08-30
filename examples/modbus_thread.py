#!/usr/bin/env python
# -*- coding: utf-8 -*-

# modbus_thread
# start a thread for polling a set of registers, display result on console

import time
from threading import Thread, Lock
from pyModbusTCP.client import ModbusClient

SERVER_HOST = "localhost"
SERVER_PORT = 502

# set global
regs = []

# init a thread lock
regs_lock = Lock()

# modbus polling thread
def polling_thread():
    global regs
    c = ModbusClient(host=SERVER_HOST, port=SERVER_PORT)
    while True:
        # keep TCP open
        if not c.is_open():
            c.open()
        reg_list = c.read_holding_registers(0,10)
        if reg_list:
            with regs_lock:
                regs = reg_list
        time.sleep(1)

# start polling thread
tp = Thread(target=polling_thread)
tp.daemon = True
tp.start()

# display loop
while True:
    with regs_lock:
        print(regs)
    time.sleep(1)

