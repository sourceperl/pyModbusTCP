#!/usr/bin/env python
# -*- coding: utf-8 -*-

# modbus_thread
# start a thread for polling a set of registers, display result on console
# exit with ctrl+c

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
    # polling loop
    while True:
        # keep TCP open
        if not c.is_open():
            c.open()
        # do modbus reading on socket
        reg_list = c.read_holding_registers(0, 10)
        # if read is ok, store result in regs (with thread lock synchronization)
        if reg_list:
            with regs_lock:
                regs = list(reg_list)
        # 1s before next polling
        time.sleep(1)


# start polling thread
tp = Thread(target=polling_thread)
# set daemon: polling thread will exit if main thread exit
tp.daemon = True
tp.start()

# display loop (in main thread)
while True:
    # print regs list (with thread lock synchronization)
    with regs_lock:
        print(regs)
    # 1s before next print
    time.sleep(1)
