Modbus/TCP server
=================

:class:`~pyModbusTCP.server.ModbusServer` answers the requests of Modbus/TCP clients (SCADA, HMI, PLC...). Each
client connection is served by its own thread.

Start and stop
--------------

.. code-block:: python

    from pyModbusTCP.server import ModbusServer

    server = ModbusServer(host='localhost', port=5020)
    # blocks until the server is stopped (by a signal handler, another thread or ctrl+c)
    server.start()

With ``no_block=True``, :meth:`~.ModbusServer.start` starts the server in a background thread and returns at once,
so the application keeps running:

.. code-block:: python

    server = ModbusServer(host='localhost', port=5020, no_block=True)
    host, port = server.start()
    print('listening on %s:%d' % (host, port))
    ...
    server.stop()

:meth:`~.ModbusServer.start` returns the address the server listens on: with ``port=0``, the operating system chooses
a free port. It raises :class:`~pyModbusTCP.server.errors.NetworkError` if the server can't listen (port already in
use, bad address...). :attr:`~.ModbusServer.is_running` tells if the server runs, :attr:`~.ModbusServer.active_sessions`
lists the connected clients.

.. note::

    Port 502, the standard Modbus/TCP port, is a privileged port on Unix systems. Don't run the server as root to
    listen on it: use a port above 1023, or see "Listen on the standard Modbus port 502 without root" in the
    `examples README <https://github.com/sourceperl/pyModbusTCP/tree/master/examples>`_.

The data bank
-------------

The server holds the 4 Modbus data spaces in a :class:`~pyModbusTCP.server.DataBank`, available as
``server.data_bank``. The application reads and writes it at any time, the access is thread-safe:

.. code-block:: python

    # values seen by the clients
    server.data_bank.set_input_registers(0, [230, 231, 229])
    server.data_bank.set_discrete_inputs(0, [True, False])

    # values written by the clients
    set_point = server.data_bank.get_holding_registers(10)
    cmd = server.data_bank.get_coils(0, 4)

.. list-table::
   :header-rows: 1
   :widths: 25 20 25 30

   * - Data space
     - Client access
     - Read by the application
     - Written by the application
   * - Coils (bits)
     - read and write
     - :meth:`~.DataBank.get_coils`
     - :meth:`~.DataBank.set_coils`
   * - Discrete inputs (bits)
     - read only
     - :meth:`~.DataBank.get_discrete_inputs`
     - :meth:`~.DataBank.set_discrete_inputs`
   * - Holding registers (16 bits)
     - read and write
     - :meth:`~.DataBank.get_holding_registers`
     - :meth:`~.DataBank.set_holding_registers`
   * - Input registers (16 bits)
     - read only
     - :meth:`~.DataBank.get_input_registers`
     - :meth:`~.DataBank.set_input_registers`

Each space has 65536 addresses by default. The size and the startup value of each space are arguments of the
:class:`~pyModbusTCP.server.DataBank` constructor:

.. code-block:: python

    from pyModbusTCP.server import DataBank, ModbusServer

    # 100 holding registers only: a client request beyond gets an "illegal data address" exception
    data_bank = DataBank(h_regs_size=100, coils_size=0, d_inputs_size=0, i_regs_size=0)
    server = ModbusServer(port=5020, data_bank=data_bank, no_block=True)

Watch the writes of the clients
-------------------------------

To react to the writes of the clients, derive :class:`~pyModbusTCP.server.DataBank` and override
:meth:`~.DataBank.on_coils_change` and :meth:`~.DataBank.on_holding_registers_change`. They are called for each
value changed by a client (not for a write of the same value, nor for the writes of the application), with the
address, the previous and the new value, and the client information:

.. code-block:: python

    from pyModbusTCP.server import DataBank, ModbusServer


    class MyDataBank(DataBank):
        def on_holding_registers_change(self, address, from_value, to_value, srv_info):
            print('register %d: %d -> %d (by %s)' % (address, from_value, to_value, srv_info.client.address))


    server = ModbusServer(port=5020, data_bank=MyDataBank())

These methods run in the thread of the client session: keep them short. An exception raised there is logged and
the client gets a "slave device failure" exception.

Customize the answers
---------------------

To change how a request is answered (compute values on the fly, filter by client, refuse some addresses),
derive :class:`~pyModbusTCP.server.DataHandler` and override its methods (``read_coils()``, ``write_h_regs()``...).
Each one returns a :class:`DataHandler.Return <pyModbusTCP.server.DataHandler.Return>` with the data, or with a Modbus
exception code:

.. code-block:: python

    from pyModbusTCP.constants import EXP_ILLEGAL_FUNCTION
    from pyModbusTCP.server import DataHandler, ModbusServer


    class MyDataHandler(DataHandler):
        def write_h_regs(self, address, words_l, srv_info):
            # only the supervisor can write the holding registers
            if srv_info.client.address != '192.168.1.20':
                return DataHandler.Return(exp_code=EXP_ILLEGAL_FUNCTION)
            return super().write_h_regs(address, words_l, srv_info)


    server = ModbusServer(host='0.0.0.0', port=5020, data_hdl=MyDataHandler())

For values computed at each request (like a clock), a :class:`~pyModbusTCP.server.DataBank` in virtual mode with an
overridden ``get_*()`` method is often simpler: see the :doc:`../examples/server_virtual_data` example.

Restrict the access
-------------------

The server has several protections, all set in the :class:`~pyModbusTCP.server.ModbusServer` constructor:

.. code-block:: python

    server = ModbusServer(
        host='192.168.1.10',                  # listen on this interface only (default: localhost)
        port=502,
        allowed_hosts=['192.168.1.0/24'],     # other clients are disconnected at once
        read_only=True,                       # every write request gets an "illegal function" exception
        max_connections=16,                   # simultaneous clients (default: 64)
        idle_timeout=60,                      # close a session without request for 60 s (default: 300)
    )

``allowed_hosts`` filters on the source IP address: it's not an authentication. See :doc:`../security` for the
deployment recommendations and the limits of these protections.

Device identification
---------------------

To answer the "Read Device Identification" function (0x2B/0x0E), give a
:class:`~pyModbusTCP.server.DeviceIdentification` to the server:

.. code-block:: python

    from pyModbusTCP.server import DeviceIdentification, ModbusServer

    device_id = DeviceIdentification(vendor_name=b'ACME', product_code=b'PLC-42', major_minor_revision=b'1.0')
    server = ModbusServer(port=5020, device_id=device_id)

Every object of the device identification can be read by any client: don't publish more than needed.

Logs
----

The server logs on the ``pyModbusTCP.server`` logger: the listen address at the ``INFO`` level, the refused
connections at the ``WARNING`` level, the errors of user code at the ``ERROR`` level, and the session events (new
client, refused or malformed request) at the ``DEBUG`` level.

.. code-block:: python

    import logging

    logging.basicConfig(level=logging.INFO)
    logging.getLogger('pyModbusTCP.server').setLevel(logging.DEBUG)

.. seealso::

    - :class:`~pyModbusTCP.server.ModbusServer` in the API reference, for all the options.
    - The server :doc:`examples <../examples/index>`.
    - ``mbtcp-server``, a ready to use :doc:`command line server <tools>`.
