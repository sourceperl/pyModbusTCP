#!/usr/bin/env python3

"""
Client with float values
~~~~~~~~~~~~~~~~~~~~~~~~

A float32 takes 2 registers: a Codec converts the floats to registers for a write, and the registers to floats
after a read. The byte order depends on the device: here ABCD (big endian), the first register holds the most
significant word (ByteOrder.CDAB if it holds the least significant word, common on PLCs).

The script starts a server on localhost (on a free port) and runs the client against it.
"""

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.codec import ByteOrder, Codec, DataType
from pyModbusTCP.server import ModbusServer

f32 = Codec(DataType.FLOAT32, ByteOrder.ABCD)

server = ModbusServer(host='localhost', port=0, no_block=True)
host, port = server.start()

with ModbusClient(host=host, port=port) as c:
    # write 2 floats at address 0 (registers 0 to 3)
    c.write_multiple_registers(0, f32.encode([21.5, -3.14]))

    # read them back
    regs = c.read_holding_registers(0, 2 * f32.words)
    if regs:
        print('registers:', [f'0x{r:04X}' for r in regs])
        # -3.14 is read as -3.140000104904175 (the nearest float32): text() gives its shortest text
        print('floats:', [f32.text(v) for v in f32.decode(regs)])

server.stop()
