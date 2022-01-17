#!/usr/bin/env python3

""" How-to add float support to ModbusClient. """

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.utils import encode_ieee, decode_ieee, \
                              long_list_to_word, word_list_to_long


class FloatModbusClient(ModbusClient):
    """A ModbusClient class with float support."""

    def read_float(self, address, number=1):
        """Read float(s) with read holding registers."""
        reg_l = self.read_holding_registers(address, number * 2)
        if reg_l:
            return [decode_ieee(f) for f in word_list_to_long(reg_l)]
        else:
            return None

    def write_float(self, address, floats_list):
        """Write float(s) with write multiple registers."""
        b32_l = [encode_ieee(f) for f in floats_list]
        b16_l = long_list_to_word(b32_l)
        return self.write_multiple_registers(address, b16_l)


if __name__ == '__main__':
    # init modbus client
    c = FloatModbusClient(host='localhost', port=502, auto_open=True)

    # write 10.0 at @0
    c.write_float(0, [10.0])

    # read @0 to 9
    float_l = c.read_float(0, 10)
    print(float_l)

    c.close()
