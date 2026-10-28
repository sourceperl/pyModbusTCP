Quick start
===========

Installation
------------

Install pyModbusTCP from PyPI in a virtual environment:

.. code-block:: bash

    python3 -m venv venv
    source venv/bin/activate        # on Windows: venv\Scripts\activate
    pip install pyModbusTCP

The library has no dependency. Two optional extras add the :doc:`command line tools <../guide/tools>`:

.. code-block:: bash

    pip install "pyModbusTCP[serial]"   # pyserial, for the Modbus TCP/RTU gateways
    pip install "pyModbusTCP[cli]"      # the mbtcp-* commands in the PATH (with pyserial)

.. tip::

    Pin the version in the requirements of your project (like ``pyModbusTCP==0.3.1``): the behavior of the library
    can change between releases (see the :doc:`../changelog`). For example, the automatic TCP open mode
    (``auto_open``) is on by default since version 0.2.0 and was off before.

.. note::

    This documentation describes version 0.4.0, only available as a pre-release (alpha) for now: pip installs the
    stable 0.3.1 by default. Ask for the pre-release explicitly, with ``pip install pyModbusTCP==0.4.0a4`` (or
    ``"pyModbusTCP[cli]==0.4.0a4"``), or with ``pip install --pre pyModbusTCP``.

To install the development version from GitHub:

.. code-block:: bash

    pip install git+https://github.com/sourceperl/pyModbusTCP.git

Package overview
----------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Module
     - Content
   * - :mod:`pyModbusTCP.client`
     - :class:`~pyModbusTCP.client.ModbusClient`: the Modbus/TCP client.
   * - :mod:`pyModbusTCP.server`
     - :class:`~pyModbusTCP.server.ModbusServer`: the Modbus/TCP server, with its data store
       (:class:`~pyModbusTCP.server.MemoryStore`), the access rules and the function handlers to customize it.
   * - :mod:`pyModbusTCP.codec`
     - :class:`~pyModbusTCP.codec.Codec`: values on several registers (32/64 bits integers, IEEE floats) in any
       byte order.
   * - :mod:`pyModbusTCP.utils`
     - Functions for Modbus data conversion: 32/64 bits values, IEEE floats, two's complement, bits.
   * - :mod:`pyModbusTCP.constants`
     - Function codes, Modbus exception codes, client error codes.
   * - ``pyModbusTCP.tools``
     - The :doc:`command line tools <../guide/tools>` (``python -m pyModbusTCP.tools``).

A first client
--------------

Read 10 holding registers at address 0 of a Modbus/TCP server:

.. code-block:: python

    from pyModbusTCP.client import ModbusClient

    # the TCP connection is opened at the first request and kept open (auto_open mode)
    c = ModbusClient(host='localhost', port=502, unit_id=1)

    regs = c.read_holding_registers(0, 10)
    if regs:
        print(regs)
    else:
        print('read error: %s' % c.last_error_as_txt)

A request never raises an exception for a network or a Modbus error: it returns ``None`` (read) or ``False``
(write), and the cause is available in :attr:`~pyModbusTCP.client.ModbusClient.last_error`. See
:doc:`../guide/client`.

A first server
--------------

Start a server on port 5020 and update its data from the application:

.. code-block:: python

    import time

    from pyModbusTCP.server import ModbusServer

    # no_block: start() returns at once, the server runs in a background thread
    server = ModbusServer(host='localhost', port=5020, no_block=True)
    server.start()

    try:
        while True:
            # clients see the current time in the holding registers 0 and 1
            now = time.localtime()
            server.store.app.holding_registers.write(0, [now.tm_hour, now.tm_min])
            time.sleep(1.0)
    finally:
        server.stop()

The server listens on ``localhost`` by default: set ``host`` to the address of a network interface to accept remote
clients. See :doc:`../guide/server`.

Try it from the command line
----------------------------

The library comes with a server and a client for the command line. In a first terminal:

.. code-block:: bash

    python -m pyModbusTCP.tools server --log-changes

In a second one:

.. code-block:: bash

    python -m pyModbusTCP.tools client -p 5020 write-registers 0 100 200
    python -m pyModbusTCP.tools client -p 5020 read-holding 0 -n 4

See :doc:`../guide/tools` for all the commands.
