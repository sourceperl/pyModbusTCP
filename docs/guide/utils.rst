Data conversion
===============

A Modbus register holds an unsigned 16 bits value, from 0 to 65535. Devices store other types on one or several
registers: signed integers, 32 or 64 bits values, IEEE floats, bit fields. The :mod:`pyModbusTCP.utils` module
converts them.

.. code-block:: python

    from pyModbusTCP import utils

Signed integers (two's complement)
----------------------------------

.. code-block:: python

    regs = [0x0000, 0xFFFF, 0x00FF, 0x8001]

    # [0, -1, 255, -32767]
    print(utils.get_list_2comp(regs, 16))
    # -1
    print(utils.get_2comp(regs[1], 16))

To write a negative value, mask it to 16 bits: ``c.write_single_register(0, -1 & 0xFFFF)``.

32 and 64 bits integers
-----------------------

A 32 bits value uses 2 registers. :func:`~pyModbusTCP.utils.word_list_to_long` merges each pair of registers,
:func:`~pyModbusTCP.utils.long_list_to_word` splits the values for a write:

.. code-block:: python

    regs = [0x0123, 0x4567, 0xdead, 0xbeef]

    # big endian, the most significant word first (default): ['0x1234567', '0xdeadbeef']
    print([hex(v) for v in utils.word_list_to_long(regs)])

    # little endian, the least significant word first: ['0x45670123', '0xbeefdead']
    print([hex(v) for v in utils.word_list_to_long(regs, big_endian=False)])

    # split for a write: ['0x123', '0x4567']
    print([hex(v) for v in utils.long_list_to_word([0x01234567])])

Both functions handle 64 bits values (4 registers each) with ``long_long=True``.

The word order is not set by the Modbus specification: check the documentation of the device, or try both.

For a signed 32 bits value, combine with the two's complement:

.. code-block:: python

    # -2
    print(utils.get_2comp(utils.word_list_to_long([0xFFFF, 0xFFFE])[0], 32))

IEEE floats
-----------

A single precision float (32 bits) uses 2 registers, a double precision one (64 bits) uses 4 registers:

.. code-block:: python

    # 32 bits float 0.3 -> 0x3e99999a
    print(hex(utils.encode_ieee(0.3)))
    # 0x3e99999a -> 0.30000001192092896 (the nearest 32 bits float of 0.3)
    print(utils.decode_ieee(0x3e99999a))

    # 64 bits float with double=True: 0x390b860bb596a559 -> 6.62606957e-34
    print(utils.decode_ieee(0x390b860bb596a559, double=True))

Read and write a float with a client:

.. code-block:: python

    # read a 32 bits float at address 0 (2 registers)
    regs = c.read_holding_registers(0, 2)
    if regs:
        value = utils.decode_ieee(utils.word_list_to_long(regs)[0])

    # write 21.5 at address 0
    c.write_multiple_registers(0, utils.long_list_to_word([utils.encode_ieee(21.5)]))

The :doc:`../examples/client_float` example adds these conversions to a ``ModbusClient`` subclass.

Bits of a register
------------------

.. code-block:: python

    # [True, False, True, False, False, False, False, False] (bit 0 first)
    print(utils.get_bits_from_int(0x05, val_size=8))

    utils.test_bit(0x05, 2)      # True
    utils.set_bit(0x00, 3)       # 0x08
    utils.reset_bit(0x0F, 0)     # 0x0E
    utils.toggle_bit(0x01, 0)    # 0x00

.. seealso::

    :mod:`pyModbusTCP.utils` in the API reference, for all the functions.
