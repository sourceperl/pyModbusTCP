Modbus/TCP client
=================

:class:`~pyModbusTCP.client.ModbusClient` sends requests to a Modbus/TCP server: a PLC, a gateway, a smart meter or
any other device that speaks Modbus/TCP.

Create a client
---------------

.. code-block:: python

    from pyModbusTCP.client import ModbusClient

    c = ModbusClient(host='192.168.1.10', port=502, unit_id=1, timeout=5.0)

The settings can also be changed later with the properties of the same name (:attr:`~.ModbusClient.host`,
:attr:`~.ModbusClient.port`, :attr:`~.ModbusClient.unit_id`, :attr:`~.ModbusClient.timeout`...). Changing the host or
the port closes the current connection.

Invalid settings raise an exception at once (``ValueError`` for a bad value, ``TypeError`` for a bad type):

.. code-block:: python

    try:
        c = ModbusClient(host='localhost', port=70000)
    except ValueError as e:
        print('bad setting: %s' % e)

``host`` is an IPv4/IPv6 address or a hostname. A hostname is ASCII only: give an internationalized domain name in
its ASCII form (punycode), the conversion is up to the application:

.. code-block:: python

    # 'café.fr' is refused (ValueError): use 'xn--caf-dma.fr'
    c = ModbusClient(host='café.fr'.encode('idna').decode())

The ``idna`` codec of the standard library follows IDNA 2003: for some names, it gives another domain than the
current IDNA 2008 (like ``'faß.de'``, encoded as ``'fass.de'``). For such names, use the ``idna`` package of PyPI
(``idna.encode('faß.de').decode()`` gives ``'xn--fa-hia.de'``).

``unit_id`` is the address of the device behind a gateway (Modbus/RTU slave address). Most Modbus/TCP devices
ignore it or expect ``1`` (the default) or ``255``.

``timeout`` is the maximum time for a whole request (send the request and receive the reply), in seconds.

Manage the TCP connection
-------------------------

With the default settings (``auto_open=True``, ``auto_close=False``), the connection is opened at the first request
and kept open for the next ones. It is opened again at the next request if the server closes it.

To open the connection before each request and close it after (useful with a server that accepts few
connections, or for a request every few minutes):

.. code-block:: python

    c = ModbusClient(host='localhost', auto_close=True)

To manage the connection yourself:

.. code-block:: python

    c = ModbusClient(host='localhost', auto_open=False)

    # open the connection for 2 reads, then close it
    if c.open():
        regs_1 = c.read_holding_registers(0, 10)
        regs_2 = c.read_holding_registers(55, 10)
        c.close()

:attr:`~.ModbusClient.is_open` gives the current state of the connection.

The client is also a context manager, the connection is closed at the end of the block:

.. code-block:: python

    with ModbusClient(host='localhost') as c:
        print(c.read_coils(0, 8))

Modbus functions
----------------

.. list-table::
   :header-rows: 1
   :widths: 12 40 10 38

   * - Domain
     - Modbus function
     - Code
     - ModbusClient method
   * - Bit
     - Read Coils
     - 0x01
     - :meth:`~.ModbusClient.read_coils`
   * -
     - Read Discrete Inputs
     - 0x02
     - :meth:`~.ModbusClient.read_discrete_inputs`
   * -
     - Write Single Coil
     - 0x05
     - :meth:`~.ModbusClient.write_single_coil`
   * -
     - Write Multiple Coils
     - 0x0F
     - :meth:`~.ModbusClient.write_multiple_coils`
   * - Register
     - Read Holding Registers
     - 0x03
     - :meth:`~.ModbusClient.read_holding_registers`
   * -
     - Read Input Registers
     - 0x04
     - :meth:`~.ModbusClient.read_input_registers`
   * -
     - Write Single Register
     - 0x06
     - :meth:`~.ModbusClient.write_single_register`
   * -
     - Write Multiple Registers
     - 0x10
     - :meth:`~.ModbusClient.write_multiple_registers`
   * -
     - Read/Write Multiple Registers
     - 0x17
     - :meth:`~.ModbusClient.write_read_multiple_registers`
   * - Diagnostic
     - Read Device Identification
     - 0x2B/0x0E
     - :meth:`~.ModbusClient.read_device_identification`
   * - Any
     - Other functions (raw PDU)
     -
     - :meth:`~.ModbusClient.custom_request`

The addresses are the protocol addresses, from 0 to 65535. A device documentation that numbers its registers from 1
(like ``40001`` for the first holding register) needs a translation: ``40001`` is address ``0``.

.. code-block:: python

    # bits: a list of bool
    coils = c.read_coils(0, 8)
    c.write_single_coil(10, True)
    c.write_multiple_coils(10, [True, False, True])

    # registers: a list of int (16 bits, from 0 to 65535)
    regs = c.read_input_registers(0, 4)
    c.write_single_register(0, 1234)
    c.write_multiple_registers(0, [44, 55, 66])

A register holds an unsigned 16 bits value: use :doc:`utils` for signed values, 32 bits values and floats.

.. _client-errors:

Handle errors
-------------

A request never raises an exception for a network or a Modbus error: a read returns ``None``, a write returns
``False``. The cause of the last error is available in the client:

.. code-block:: python

    regs = c.read_holding_registers(0, 10)
    if regs is None:
        print('error: %s' % c.last_error_as_txt)
        # the server answered with a Modbus exception (illegal address, device busy...)
        if c.last_error == MB_EXCEPT_ERR:
            print('modbus exception: %s' % c.last_except_as_txt)

(with ``from pyModbusTCP.constants import MB_EXCEPT_ERR``)

:attr:`~.ModbusClient.last_error` is one of these codes (constants of :mod:`pyModbusTCP.constants`):

.. list-table::
   :header-rows: 1
   :widths: 25 8 67

   * - Constant
     - Value
     - Meaning
   * - ``MB_NO_ERR``
     - 0
     - No error: the last request succeeded.
   * - ``MB_CONNECT_ERR``
     - 2
     - The connection failed: unknown host name, server down, wrong port, firewall...
   * - ``MB_SEND_ERR``
     - 3
     - The request can't be sent.
   * - ``MB_RECV_ERR``
     - 4
     - No valid reply: the server closed the connection, or its reply is invalid or doesn't match the request.
   * - ``MB_TIMEOUT_ERR``
     - 5
     - No complete reply before ``timeout``.
   * - ``MB_EXCEPT_ERR``
     - 7
     - The server answered with a Modbus exception: see :attr:`~.ModbusClient.last_except`.
   * - ``MB_SOCK_CLOSE_ERR``
     - 9
     - No open connection for the request (with ``auto_open=False``, call :meth:`~.ModbusClient.open` first).

On a Modbus exception, :attr:`~.ModbusClient.last_except` is the exception code sent by the server (like
``EXP_DATA_ADDRESS``, 2: the address doesn't exist on the device), :attr:`~.ModbusClient.last_except_as_txt` and
:attr:`~.ModbusClient.last_except_as_full_txt` describe it.

Invalid arguments are programming errors: they raise ``ValueError`` (out of range address or quantity...) or
``TypeError``, before any network I/O.

Use a client from several threads
---------------------------------

A :class:`~pyModbusTCP.client.ModbusClient` instance is not thread-safe: use one client per thread, or protect a
shared client with a lock.

.. code-block:: python

    import threading

    lock = threading.Lock()

    def read_regs():
        with lock:
            return c.read_holding_registers(0, 10)

See the :doc:`../examples/client_thread` example for a polling thread.

Debug
-----

The client logs every frame sent and received at the ``DEBUG`` level, on the ``pyModbusTCP.client`` logger:

.. code-block:: python

    import logging

    from pyModbusTCP.client import ModbusClient

    logging.basicConfig()
    logging.getLogger('pyModbusTCP.client').setLevel(logging.DEBUG)

    c = ModbusClient(host='localhost', port=502)
    c.read_coils(0)

Each request then shows the MBAP header (in brackets) and the PDU of the frames::

    DEBUG:pyModbusTCP.client:(localhost:502:1) Tx [8F 8A 00 00 00 06 01] 01 00 00 00 01
    DEBUG:pyModbusTCP.client:(localhost:502:1) Rx [8F 8A 00 00 00 04 01] 01 01 00

The ``mbtcp-client`` :doc:`command line tool <tools>` (option ``-d``) gives the same trace without writing any code.

.. seealso::

    - :class:`~pyModbusTCP.client.ModbusClient` in the API reference, for all the methods and their arguments.
    - The client :doc:`examples <../examples/index>`.
