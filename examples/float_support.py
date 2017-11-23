#!/usr/bin/env python
# -*- coding: utf-8 -*-

# how-to add float support to ModbusClient

from pyModbusTCP.client import ModbusClient
from pyModbusTCP import utils


class FloatModbusClient(ModbusClient):
    def read_float(self, address, number=1):
        reg_l = self.read_holding_registers(address, number * 2)
        if reg_l:
            return [utils.decode_ieee(f) for f in utils.word_list_to_long(reg_l)]
        else:
            return None

    def write_float(self, address, floats_list):
        b32_l = [utils.encode_ieee(f) for f in floats_list]
        b16_l = utils.long_list_to_word(b32_l)
        return self.write_multiple_registers(address, b16_l)


c = FloatModbusClient(host='localhost', port=502, auto_open=True)

# write 10.0 at @0
c.write_float(0, [10.0])

# read @0 to 9
float_l = c.read_float(0, 10)
print(float_l)

c.close()
