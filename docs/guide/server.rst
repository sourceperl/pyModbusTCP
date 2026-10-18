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

The data store
--------------

The server reads and writes the 4 Modbus data spaces in a data store, available as ``server.store``. The default one
is a :class:`~pyModbusTCP.server.MemoryStore`. The application accesses it with ``server.store.app``, at any time
(the access is thread-safe):

.. code-block:: python

    app = server.store.app

    # values seen by the clients
    app.input_registers.write(0, [230, 231, 229])
    app.discrete_inputs.write(0, [True, False])

    # values written by the clients
    set_point = app.holding_registers.read(10)[0]
    cmd = app.coils.read(0, 4)

.. list-table::
   :header-rows: 1
   :widths: 25 20 55

   * - Data space
     - Client access
     - Application access
   * - Coils (bits)
     - read and write
     - ``app.coils.read()``, ``app.coils.write()``
   * - Discrete inputs (bits)
     - read only
     - ``app.discrete_inputs.read()``, ``app.discrete_inputs.write()``
   * - Holding registers (16 bits)
     - read and write
     - ``app.holding_registers.read()``, ``app.holding_registers.write()``
   * - Input registers (16 bits)
     - read only
     - ``app.input_registers.read()``, ``app.input_registers.write()``

A register is a 16 bits unsigned int: a value out of 0 to 65535 raises a ``ValueError``, a value that is not an int
(a str, a float) a ``TypeError``. An address out of the space raises
:class:`~pyModbusTCP.server.IllegalDataAddress`, and nothing is written.

Each space has 65536 addresses by default. The sizes and the startup values are arguments of
:class:`~pyModbusTCP.server.MemoryStore`:

.. code-block:: python

    from pyModbusTCP.server import MemoryStore, ModbusServer

    # 100 holding registers only: a client request beyond gets an "illegal data address" exception
    store = MemoryStore(0, holding_registers=100)
    server = ModbusServer(port=5020, store=store, no_block=True)

Typed values
~~~~~~~~~~~~

The registers have a typed access for the values on several registers, with a :ref:`Codec <utils-codec>` (any type
and byte order), or with shortcuts for the common types (the first register holds the most significant word,
``big_endian=False`` for the other order):

.. code-block:: python

    from pyModbusTCP.codec import ByteOrder, Codec, DataType

    VOLTS = Codec(DataType.FLOAT32, ByteOrder.CDAB)
    regs = server.store.app.input_registers
    regs.write_values(20, [230.4, 231.0, 229.8], VOLTS)    # registers 20 to 25, atomic
    volts = regs.read_values(20, VOLTS, 3)

    regs.write_float32(0, 230.4)          # IEEE 754 single precision, registers 0 and 1
    regs.write_int32(2, -1500)            # 2's complement, registers 2 and 3
    regs.write_uint32(4, 123456, big_endian=False)
    total = regs.read_float64(10)         # double precision, registers 10 to 13

Transactions
------------

Every request of a client runs in a transaction of the store: no other request (or application access) can run
during it, and if it fails, every value it wrote is restored. The function "write/read multiple registers" (0x17)
writes then reads in one transaction: its response contains its own write, and if the read fails (an invalid
address), the write is cancelled.

The application uses a transaction to change several values at once, even in several spaces: a client never sees a
part of the update.

.. code-block:: python

    with server.store.app.transaction() as tx:
        tx.write(Space.INPUT_REGISTERS, 0, [voltage, current])
        tx.write(Space.DISCRETE_INPUTS, 0, [alarm])

A transaction holds the lock of the store: keep it short. An exception raised in the ``with`` block restores the
values written in it, then goes on.

Watch the changes
-----------------

To react to the writes of the clients, subscribe a listener to the store. It is called once per request that changed
some values, after the transaction is committed, with a :class:`~pyModbusTCP.server.ChangeEvent`: the request context
(client, unit id, function code) and the list of the changes.

.. code-block:: python

    from pyModbusTCP.server import ChangeEvent, MemoryStore, Space

    def on_change(event: ChangeEvent) -> None:
        for change in event.of(Space.HOLDING_REGISTERS):
            print(f'register {change.address}: {change.old} -> {change.new} (by {event.ctx.client.address})')

    store = MemoryStore()
    store.subscribe(on_change)

- Only the values that really change are notified. A client that writes 5 in a register that already holds 5 gives no
  change for this register, and a request that changes nothing gives no event at all.
- Each address appears at most once in an event, with its value before the request (``old``) and after it (``new``).
  A standard function writes each address once, but a custom function or an application transaction can write the
  same register several times: for 5, then 7, then 9, the event has one change from 5 to 9. If the register ends with
  its value from before the request (5, then 7, then 5 again), there is no change.
- ``subscribe(..., app_writes=True)`` also notifies the writes of the application (``event.ctx`` is then ``None``).
- By default, the listener runs in the thread of the client session: the response waits for it, keep it short. With
  ``subscribe(..., threaded=True)``, it runs in a thread of the store, in order, and never delays the clients. The
  events wait in a bounded queue (``MemoryStore.notify_queue_size``, 10000 events): if the listener can't keep up,
  the events that don't fit are dropped, with a warning in the log (at most one per 10 s, with the count).
- The listener runs without the lock: it can use the store (like writing an acknowledge). The value it writes is not in
  the response of the request that triggered it.
- An exception raised by a listener is logged: the request is committed anyway, the client gets its response.

``subscribe()`` returns a function that unsubscribes the listener.

Computed values
---------------

A range of addresses can be computed at each access instead of being kept in memory (like a clock, or the values of
a device read on demand):

.. code-block:: python

    def clock(address: int, count: int, ctx) -> list[int]:
        now = time.localtime()
        return [now.tm_hour, now.tm_min, now.tm_sec][address - 100:address - 100 + count]

    store = MemoryStore()
    store.map(Space.INPUT_REGISTERS, 100, 3, read=clock)

``read(address, count, ctx)`` returns the values (``ctx`` is ``None`` for the application). Give a ``write(address,
values, ctx)`` function for a writable range: without it, a write gets an "illegal data address" exception. If the
transaction fails afterwards, the store restores the values in memory, not what the ``write`` function did. A mapped
range can be beyond the size of its space: ``MemoryStore(0)`` is a store with mapped ranges only. The functions run in
the transaction: keep them fast. A function that returns a wrong number of values, or an invalid value, gives a "slave
device failure" to the client (and an error in the log).

A ``write`` function runs in the transaction of the request, and the store accesses it does join this transaction:
it can compute a result that the same request reads back. With function 0x17, a client writes a command and reads its
result in one request; a ``ModbusException`` raised by the function cancels the whole request. See the
:doc:`../examples/server_mailbox` example.

Access rules
------------

A wrapper is a store stacked on another one, that checks each access of a client:

.. code-block:: python

    from pyModbusTCP.server import AccessControl, Guard, MemoryStore, ReadOnly, Space

    store = MemoryStore()
    store = ReadOnly(store, spaces=[Space.COILS])                   # the clients can't write the coils
    store = AccessControl(store, write_hosts=['192.168.1.20'])      # only the supervisor writes
    store = Guard(store, lambda ctx, access, space, address, count: address + count <= 1000)
    server = ModbusServer(port=5020, store=store)

- :class:`~pyModbusTCP.server.ReadOnly`: the writes of some spaces (default: every space) get an "illegal function"
  exception.
- :class:`~pyModbusTCP.server.AccessControl`: the reads and the writes are allowed to some client addresses or
  networks only ("illegal function" exception). ``read_hosts`` and ``write_hosts`` follow the convention of every
  allow list: ``None`` for every client, ``[]`` for no client at all.
- :class:`~pyModbusTCP.server.Guard`: a function ``check(ctx, access, space, address, count)`` returns ``False`` to
  refuse an access ("illegal data address"), or raises a :class:`~pyModbusTCP.server.ModbusException` for another
  code.
- Derive :class:`~pyModbusTCP.server.StoreWrapper` and override its ``check()`` method for other rules.

The rules only apply to the clients: the application (``store.app``) always has a full access. A refused access
raises an exception in the transaction: everything the request wrote is restored.

:class:`~pyModbusTCP.server.AccessControl` and the ``allowed_hosts`` option of the server (see `Restrict the access`_)
both filter on the client address, but at different levels:

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * -
     - ``allowed_hosts`` (ModbusServer)
     - :class:`~pyModbusTCP.server.AccessControl` (store wrapper)
   * - Checks
     - the TCP connection
     - each access to the data
   * - A refused client
     - is disconnected at once, before any request
     - stays connected, each refused request gets an "illegal function" exception
   * - Granularity
     - all or nothing
     - reads and writes apart
   * - Covers
     - every function: device identification, custom functions, fallback
     - only the functions that use the store
   * - Sessions
     - a refused client takes no session
     - a refused client takes a session (see ``max_connections``)

Use them together: ``allowed_hosts`` for the clients that can connect, ``AccessControl`` for what each of them can do
(like ``write_hosts`` for the ones that can write). See the :doc:`../examples/server_allow` example. Neither is an
authentication: both trust the source IP address, a firewall is still needed.

Custom functions
----------------

The server routes each request to the handler of its function code, in ``server.functions``. A handler takes a
:class:`~pyModbusTCP.server.Request` and returns the response PDU (bytes, the function code first). It raises a
:class:`~pyModbusTCP.server.ModbusException` (like :class:`~pyModbusTCP.server.IllegalDataAddress`) to answer with an
exception:

.. code-block:: python

    from pyModbusTCP.server import ModbusServer, Request, Space

    server = ModbusServer(port=5020)

    @server.function(0x41)
    def sum_of_two_registers(req: Request) -> bytes:
        (address,) = req.unpack('>H', 1, 3)             # a short request: "illegal data value"
        with req.transaction() as tx:                   # the store of the unit id, with the request context
            values = tx.read(Space.HOLDING_REGISTERS, address, 2)
        return req.reply('I', sum(values))              # function code + a 32 bits unsigned int

- A standard function can be replaced, and called from the new handler (``STANDARD_FUNCTIONS[0x06](req)``).
- ``del server.functions[code]`` removes a function ("illegal function" exception).
- ``ModbusServer(functions={})`` starts without any function, ``fallback=handler`` handles the function codes without
  handler (like a gateway that relays every request).
- An unexpected exception of a handler is logged, the client gets a "slave device failure" and its session goes on.

See the :doc:`../examples/server_custom_function` example.

Several devices
---------------

To simulate several devices behind one address (like a gateway), give one store per unit id. A request for another
unit id gets a "gateway path unavailable" exception:

.. code-block:: python

    meter, valve = MemoryStore(), MemoryStore()
    server = ModbusServer(port=5020, store={1: meter, 2: valve})
    meter.app.input_registers.write_float32(0, 230.0)

``server.stores`` gives the stores by unit id (``server.store`` is only available with a single store). See the
:doc:`../examples/server_multi_unit` example.

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
        max_connections_per_host=4,           # simultaneous connections of one client host (default: 16)
        idle_timeout=60,                      # close a session without request for 60 s (default: 300)
    )

Like every allow list of pyModbusTCP, ``allowed_hosts`` is ``None`` for every client (the default) and ``[]`` for no
client at all: the server then refuses every connection, and logs a warning at startup (an empty list from a
configuration is often a mistake).

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

Migrating from DataBank and DataHandler
---------------------------------------

The data model of the server changed in 0.4.0, without compatibility with the previous versions:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Before
     - Now
   * - ``ModbusServer(data_bank=DataBank())``
     - ``ModbusServer(store=MemoryStore())``
   * - ``server.data_bank.set_holding_registers(0, [1, 2])``
     - ``server.store.app.holding_registers.write(0, [1, 2])``
   * - ``server.data_bank.get_coils(0, 4)``
     - ``server.store.app.coils.read(0, 4)``
   * - ``DataBank(h_regs_size=100, coils_size=0, ...)``
     - ``MemoryStore(0, holding_registers=100)``
   * - A method returning ``None`` for an address out of the space
     - :class:`~pyModbusTCP.server.IllegalDataAddress` is raised
   * - ``DataBank.on_holding_registers_change()`` (one call per register)
     - ``store.subscribe(listener)`` (one :class:`~pyModbusTCP.server.ChangeEvent` per request)
   * - ``DataBank(virtual_mode=True)`` with an overridden ``get_*()`` method
     - ``MemoryStore(0)`` and ``store.map(space, address, count, read)``
   * - A ``DataHandler`` subclass that filters the clients
     - :class:`~pyModbusTCP.server.AccessControl`, :class:`~pyModbusTCP.server.ReadOnly`,
       :class:`~pyModbusTCP.server.Guard` or a :class:`~pyModbusTCP.server.StoreWrapper` subclass
   * - ``DataHandler.Return(exp_code=EXP_DATA_ADDRESS)``
     - ``raise IllegalDataAddress()``
   * - ``srv_info.client.address``
     - ``ctx.client.address`` (a :class:`~pyModbusTCP.server.RequestContext`, also with the unit id and the function
       code)
   * - ``ext_engine=engine(session_data)``
     - ``functions={}, fallback=handler(request)`` (a handler returns the response PDU, raises a
       :class:`~pyModbusTCP.server.ModbusException`)

Two behaviors changed: an exception of a change listener no longer gives a "slave device failure" to the client (the
request is committed, the error is logged), and ``read_only=True`` now also applies to a fallback handler.

.. seealso::

    - :class:`~pyModbusTCP.server.ModbusServer` in the API reference, for all the options.
    - The server :doc:`examples <../examples/index>`.
    - ``mbtcp-server``, a ready to use :doc:`command line server <tools>`.
