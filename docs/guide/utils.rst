Data conversion
===============

A Modbus register holds an unsigned 16 bits value, from 0 to 65535. Devices store other types on one or several
registers: signed integers, 32 or 64 bits values, IEEE floats, bit fields. A :class:`~pyModbusTCP.codec.Codec`
converts the values of a type in any byte order; the :mod:`pyModbusTCP.utils` module has the older functions and
the bit operations.

.. _utils-codec:

Values on several registers: Codec
----------------------------------

A :class:`~pyModbusTCP.codec.Codec` converts the values of a :class:`~pyModbusTCP.codec.DataType` to registers
(for a write) and registers to values (after a read), in a :class:`~pyModbusTCP.codec.ByteOrder`:

.. code-block:: python

    from pyModbusTCP.codec import ByteOrder, Codec, DataType

    f32 = Codec(DataType.FLOAT32)         # IEEE 754 single precision, big endian (ByteOrder.ABCD)
    f32.encode([3.14, -2.5])              # [0x4048, 0xF5C3, 0xC020, 0x0000]
    f32.decode([0x4048, 0xF5C3])          # [3.140000104904175]: the nearest float32 of 3.14
    f32.text(3.140000104904175)           # '3.14': the shortest text of this float32

    Codec(DataType.INT32).decode([0xFFFF, 0xFFFE])                     # [-2]
    Codec(DataType.UINT64, ByteOrder.WORD_SWAP).encode([1])            # [1, 0, 0, 0]

The data types are ``UINT16``, ``INT16`` (1 register), ``UINT32``, ``INT32``, ``FLOAT32`` (2 registers), ``UINT64``,
``INT64`` and ``FLOAT64`` (4 registers); ``codec.words`` gives the number of registers of a value. The enums make a
typo an error at once (and are listed by the completion of an IDE): ``Codec('float32')`` raises
:class:`TypeError`. ``encode()`` raises :class:`TypeError` for a value of another type (a float for an integer
type) and :class:`ValueError` out of the range of the type: a wrong value is never truncated.

The Modbus specification doesn't say how a device stores a value on several registers: each device chooses. The
byte orders are named in the notation of most Modbus software, ``A`` being the most significant byte of the value,
and each one has an alias:

==================  ======================  ==========================  ========================
ByteOrder           Alias                   Meaning                     FLOAT32 3.14
==================  ======================  ==========================  ========================
``ABCD`` (default)  ``BIG_ENDIAN``          big endian                  ``0x4048, 0xF5C3``
``CDAB``            ``WORD_SWAP``           word swap (common on PLCs)  ``0xF5C3, 0x4048``
``BADC``            ``BYTE_SWAP``           byte swap                   ``0x4840, 0xC3F5``
``DCBA``            ``LITTLE_ENDIAN``       word swap and byte swap     ``0xC3F5, 0x4840``
==================  ======================  ==========================  ========================

For a 64 bits value, ``CDAB`` reverses the order of the 4 registers. To find the order of a device, read a known
value with each order (``mbtcp-client read-holding 0 -T float32 -b CDAB``, see :doc:`tools`).

A few devices store a 64 bits value in another order, like two 32 bits values in word swap. For these rare cases, a
codec of a 64 bits type also takes a :class:`~pyModbusTCP.codec.ByteOrder64`, the 8 orders of the 8 bytes (``A`` to
``H``) on the 4 registers:

==================  ========================  ===============================================
ByteOrder64         Registers                 Like
==================  ========================  ===============================================
``ABCDEFGH``        ``AB CD EF GH``           ``ByteOrder.ABCD``
``CDABGHEF``        ``CD AB GH EF``           two 32 bits values in ``CDAB``
``EFGHABCD``        ``EF GH AB CD``           the 32 bits halves swapped
``GHEFCDAB``        ``GH EF CD AB``           ``ByteOrder.CDAB``
``BADCFEHG``        ``BA DC FE HG``           ``ByteOrder.BADC``
``DCBAHGFE``        ``DC BA HG FE``           two 32 bits values in ``DCBA``
``FEHGBADC``        ``FE HG BA DC``           the halves swapped, byte swap
``HGFEDCBA``        ``HG FE DC BA``           ``ByteOrder.DCBA``
==================  ========================  ===============================================

.. code-block:: python

    from pyModbusTCP.codec import ByteOrder64

    energy = Codec(DataType.UINT64, ByteOrder64.CDABGHEF)
    energy.encode([0x0102030405060708])           # [0x0304, 0x0102, 0x0708, 0x0506]

A ``ByteOrder64`` is only for a ``Codec``: the shortcuts of the server store and ``mbtcp-client`` take a
``ByteOrder`` (use ``read_values()`` and ``write_values()`` with the codec in the store).

Read and write floats with a client:

.. code-block:: python

    f32 = Codec(DataType.FLOAT32, ByteOrder.CDAB)

    # write 2 floats at address 0 (registers 0 to 3)
    c.write_multiple_registers(0, f32.encode([21.5, 22.0]))

    # read them back
    regs = c.read_holding_registers(0, 2 * f32.words)
    if regs:
        print(f32.decode(regs))           # [21.5, 22.0]

A codec is immutable: create one per data type and byte order of a device, and reuse it. The data store of the
server reads and writes typed values with it: ``store.app.holding_registers.write_values(0, [21.5], f32)`` (see
:doc:`server`). See also the :doc:`../examples/client_float` example.

The utils functions
-------------------

.. code-block:: python

    from pyModbusTCP import utils

Signed integers (two's complement)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    regs = [0x0000, 0xFFFF, 0x00FF, 0x8001]

    # [0, -1, 255, -32767]
    print(utils.get_list_2comp(regs, 16))
    # -1
    print(utils.get_2comp(regs[1], 16))

To write a negative value, mask it to 16 bits: ``c.write_single_register(0, -1 & 0xFFFF)``.

32 and 64 bits integers
~~~~~~~~~~~~~~~~~~~~~~~

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

``big_endian=False`` is the ``CDAB`` order of a codec: these functions have no byte swap (``BADC``, ``DCBA``).

For a signed 32 bits value, combine with the two's complement:

.. code-block:: python

    # -2
    print(utils.get_2comp(utils.word_list_to_long([0xFFFF, 0xFFFE])[0], 32))

IEEE floats
~~~~~~~~~~~

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

Bits of a register
~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # [True, False, True, False, False, False, False, False] (bit 0 first)
    print(utils.get_bits_from_int(0x05, val_size=8))

    utils.test_bit(0x05, 2)      # True
    utils.set_bit(0x00, 3)       # 0x08
    utils.reset_bit(0x0F, 0)     # 0x0E
    utils.toggle_bit(0x01, 0)    # 0x00

.. seealso::

    :mod:`pyModbusTCP.utils` in the API reference, for all the functions.
