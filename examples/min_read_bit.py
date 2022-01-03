#!/usr/bin/env python3
from pyModbusTCP.client import ModbusClient

# minimal code: read 3 coils at @1000 on localhost server, print result
print(f'coils={ModbusClient(auto_open=True).read_coils(1000,3)}')
