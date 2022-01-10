Quick start guide
=================

Overview of the package
-----------------------

pyModbusTCP give access to modbus/TCP server through the ModbusClient object.
This class is define in the client module.

Since version 0.1.0, a server is available as ModbusServer class. This server
is currently in test (API can change at any time).

To deal with frequent need of modbus data mangling (for example convert 32 bits
IEEE float to 2x16 bits words) a special module named utils provide some helpful
functions.

**Package map:**

.. image:: map.png
   :scale: 75 %

Package setup
-------------

from PyPi::

    # for Python 2
    sudo pip2 install pyModbusTCP
    # or for Python 3
    sudo pip3 install pyModbusTCP
    # upgrade from an older release
    sudo pip3 install pyModbusTCP --upgrade

from Github::

    git clone https://github.com/sourceperl/pyModbusTCP.git
    cd pyModbusTCP
    # here change "python" by your python target(s) version(s) (like python3.2)
    sudo python setup.py install

ModbusClient: init
------------------

init module from constructor (raise ValueError if host/port error)::

    from pyModbusTCP.client import ModbusClient
    try:
        c = ModbusClient(host='localhost', port=502)
    except ValueError:
        print("Error with host or port params")

you can also init module with property::

    from pyModbusTCP.client import ModbusClient

    c = ModbusClient()
    c.host = 'localhost'
    c.port = 502

ModbusClient: TCP link management
---------------------------------

Since version 0.2.0, "auto open" mode is the default behaviour to deal with TCP open/close.

The "auto open" mode keep the TCP connection always open, so the default constructor is::

        c = ModbusClient(host="localhost", auto_open=True, auto_close=False)

It's also possible to open/close TCP socket before and after each request::

        c = ModbusClient(host="localhost", auto_open=True, auto_close=True)

Another way to deal with connection is to manually set it. Like this::

        c = ModbusClient(host="localhost", auto_open=False, auto_close=False)

        # open the socket for 2 reads then close it.
        if c.open():
            regs_list_1 = c.read_holding_registers(0, 10)
            regs_list_2 = c.read_holding_registers(55, 10)
            c.close()

ModbusClient: available modbus requests functions
-------------------------------------------------

See http://en.wikipedia.org/wiki/Modbus for full table.

+------------+------------------------------+---------------+---------------------------------------------------------------------+
| Domain     | Function name                | Function code | ModbusClient function                                               |
+============+==============================+===============+=====================================================================+
| Bit        | Read Discrete Inputs         | 2             | :py:meth:`~pyModbusTCP.client.ModbusClient.read_discrete_inputs`    |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Read Coils                   | 1             | :py:meth:`~pyModbusTCP.client.ModbusClient.read_coils`              |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Write Single Coil            | 5             | :py:meth:`~pyModbusTCP.client.ModbusClient.write_single_coil`       |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Write Multiple Coils         | 15            | :py:meth:`~pyModbusTCP.client.ModbusClient.write_multiple_coils`    |
+------------+------------------------------+---------------+---------------------------------------------------------------------+
| Register   | Read Input Registers         | 4             | :py:meth:`~pyModbusTCP.client.ModbusClient.read_input_registers`    |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Read Holding Registers       | 3             | :py:meth:`~pyModbusTCP.client.ModbusClient.read_holding_registers`  |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Write Single Register        | 6             | :py:meth:`~pyModbusTCP.client.ModbusClient.write_single_register`   |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Write Multiple Registers     | 16            | :py:meth:`~pyModbusTCP.client.ModbusClient.write_multiple_registers`|
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Read/Write Multiple Registers| 23            | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Mask Write Register          | 22            | n/a                                                                 |
+------------+------------------------------+---------------+---------------------------------------------------------------------+
| File       | Read FIFO Queue              | 24            | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Read File Record             | 20            | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Write File Record            | 21            | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Read Exception Status        | 7             | n/a                                                                 |
+------------+------------------------------+---------------+---------------------------------------------------------------------+
| Diagnostic | Diagnostic                   | 8             | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Get Com Event Counter        | 11            | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Get Com Event Log            | 12            | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Report Slave ID              | 17            | n/a                                                                 |
|            +------------------------------+---------------+---------------------------------------------------------------------+
|            | Read Device Identification   | 43            | n/a                                                                 |
+------------+------------------------------+---------------+---------------------------------------------------------------------+

ModbusClient: debug mode
------------------------

If need, you can enable a debug mode for ModbusClient like this::

    from pyModbusTCP.client import ModbusClient
    c = ModbusClient(host="localhost", port=502, debug=True)

or::

    c.debug = True

when debug is enable all debug message is print on console and you can see
modbus frame::

    c.read_holding_registers(0, 4)

print::

    Tx
    [E7 53 00 00 00 06 01] 03 00 00 00 04
    Rx
    [E7 53 00 00 00 0B 01] 03 08 00 00 00 6F 00 00 00 00
    [0, 111, 0, 0]


utils module: Modbus data mangling
----------------------------------

When we have to deal with the variety types of registers of PLC device, we often
need some data mangling. Utils part of pyModbusTCP can help you in this task.
Now, let's see some use cases.

- deal with negative numbers (two's complement)::

    from pyModbusTCP import utils

    list_16_bits = [0x0000, 0xFFFF, 0x00FF, 0x8001]

    # show "[0, -1, 255, -32767]"
    print(utils.get_list_2comp(list_16_bits, 16))

    # show "-1"
    print(utils.get_2comp(list_16_bits[1], 16))

More at http://en.wikipedia.org/wiki/Two%27s_complement

- convert integer of val_size bits (default is 16) to an array of boolean::

    from pyModbusTCP import utils

    # show "[True, False, True, False, False, False, False, False]"
    print(utils.get_bits_from_int(0x05, val_size=8))

- read of 32 bits registers (also know as long format)::

    from pyModbusTCP import utils

    list_16_bits = [0x0123, 0x4567, 0xdead, 0xbeef]

    # big endian sample (default)
    list_32_bits = utils.word_list_to_long(list_16_bits)
    # show "['0x1234567', '0xdeadbeef']"
    print([hex(i) for i in list_32_bits])

    # little endian sample
    list_32_bits = utils.word_list_to_long(list_16_bits, big_endian=False)
    # show "['0x45670123', '0xbeefdead']"
    print([hex(i) for i in list_32_bits])

- IEEE single/double precision floating-point::

    from pyModbusTCP import utils

    # 32 bits IEEE single precision
    # encode : python float 0.3 -> int 0x3e99999a
    # display "0x3e99999a"
    print(hex(utils.encode_ieee(0.3)))
    # decode: python int 0x3e99999a -> float 0.3
    # show "0.300000011921" (it's not 0.3, precision leak with float...)
    print(utils.decode_ieee(0x3e99999a))

    # 64 bits IEEE double precision
    # encode: python float 6.62606957e-34 -> int 0x390b860bb596a559
    # display "0x390b860bb596a559"
    print(hex(utils.encode_ieee(6.62606957e-34, double=True)))
    # decode: python int 0x390b860bb596a559 -> float 6.62606957e-34
    # display "6.62606957e-34"
    print(utils.decode_ieee(0x390b860bb596a559, double=True))

