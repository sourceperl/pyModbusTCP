Command line tools
==================

pyModbusTCP comes with 4 command line tools, built on the library:

.. list-table::
   :header-rows: 1
   :widths: 22 33 45

   * - Command
     - Without pyModbusTCP-cli
     - Role
   * - ``mbtcp-server``
     - ``python -m pyModbusTCP.tools server``
     - Modbus/TCP server: test a client, a SCADA, a gateway...
   * - ``mbtcp-client``
     - ``python -m pyModbusTCP.tools client``
     - Modbus/TCP client: read and write requests from the command line.
   * - ``mbtcp-tcp2rtu``
     - ``python -m pyModbusTCP.tools tcp2rtu``
     - Gateway: Modbus/TCP clients to Modbus/RTU slaves on a serial line.
   * - ``mbtcp-rtu2tcp``
     - ``python -m pyModbusTCP.tools rtu2tcp``
     - Gateway: a Modbus/RTU master on a serial line to a Modbus/TCP server.

Installation
------------

The tools are part of the library: ``python -m pyModbusTCP.tools`` runs them without any other package. The
gateways need pyserial, an optional dependency:

.. code-block:: bash

    pip install "pyModbusTCP[serial]"

A plain ``pip install pyModbusTCP`` puts no command in the PATH. The ``mbtcp-*`` commands come with the
pyModbusTCP-cli package (it contains no code, only the commands and pyserial):

.. code-block:: bash

    # as isolated command line tools (recommended)
    pipx install pyModbusTCP-cli

    # or in a virtual environment, with the library
    pip install "pyModbusTCP[cli]"

pyModbusTCP-cli always has the version of the library it requires.

.. note::

    The tools are new in version 0.4.0, only available as a pre-release (alpha) for now: their options and their
    output may still change. ``pip install "pyModbusTCP[cli]"`` without a version installs the stable 0.3.1,
    without the tools: use ``pip install "pyModbusTCP[cli]==0.4.0a2"`` (or ``--pre``).

Examples
--------

.. code-block:: bash

    # server on localhost:5020, log the writes of the clients
    mbtcp-server --log-changes

    # read 4 holding registers from address 0, write some values
    mbtcp-client -p 5020 read-holding 0 -n 4
    mbtcp-client -p 5020 write-registers 0 100 200 0x1234
    # write 3 registers from address 0, then read 4 registers from address 0, in one request (function 0x17)
    mbtcp-client -p 5020 write-read 0 20 22 1 -n 4
    mbtcp-client -p 5020 write-coil 10 on

    # float32 values on 2 registers, the first register holds the least significant word (CDAB)
    mbtcp-client -p 5020 write-registers 20 3.14 -2.5 -T float32 -b CDAB
    mbtcp-client -p 5020 read-holding 20 -n 2 -T float32 -b CDAB

    # read a device every second, show the frames
    mbtcp-client -H 192.168.1.10 -d read-input-regs 0 -n 2 -i 1

    # TCP clients of the 192.168.1.0/24 network can read (only) the RTU slaves of /dev/ttyUSB0
    mbtcp-tcp2rtu -H 0.0.0.0 -A 192.168.1.0/24 --read-only -b 19200 --parity E /dev/ttyUSB0

    # an RTU master on /dev/ttyUSB0 reads the Modbus/TCP server 192.168.1.10 (the gateway is the RTU slave 30)
    mbtcp-rtu2tcp -a 30 -H 192.168.1.10 /dev/ttyUSB0

Broadcast through mbtcp-tcp2rtu
-------------------------------

The unit id 0 is the broadcast address of the serial line: every slave executes the request, none of them answers.
With ``--allow-broadcast``, the gateway queues a broadcast request for the bus and answers the TCP client at once with
the normal response of the function (the echo of the request for 0x05, 0x06, 0x15 and 0x16, the address and the
quantity for 0x0F and 0x10): a client never waits for a timeout. The bus then stays silent for
``--broadcast-delay`` seconds (default 0.1), so that the slaves execute the request before the next frame.

A broadcast of another function (a read) is refused with exception 0x01, and a broadcast is never relayed with
``--read-only``. The response only means "queued for the bus": no slave confirms a broadcast.

The response comes as soon as the broadcast is in the queue of the bus, in turn with the other requests of its client
host (a request after a broadcast is never written before it). A few broadcasts can wait for the bus:

- ``--broadcast-buffer N`` (default 4, up to 8): with N broadcasts waiting, the next one gets exception 0x06 (slave
  device busy) at once.
- ``--broadcast-max-age S`` (default 10 s, up to 60): a broadcast that waited longer for the bus (slaves that time
  out, a serial port lost) is dropped, never written, and logged.

A client that sends broadcasts too fast never builds a long backlog: an old write is never applied long after it
stopped (a pyModbusTCP client spaces its broadcasts by ``broadcast_interval``, 0.15 s by default). A broadcast lost on
the gateway (dropped, or a serial port lost) is never reported to the client: it already got its response.

.. code-block:: bash

    mbtcp-tcp2rtu --allow-broadcast --broadcast-delay 0.2 --broadcast-buffer 8 -b 19200 /dev/ttyUSB0

RS-485 mode of the gateways
---------------------------

An USB-RS485 adapter switches its transceiver between send and receive by itself: nothing to set. The UART of an
embedded board often drives the transceiver with its RTS line: ``--rs485`` enables the RS-485 mode of the serial
driver (Linux), the kernel switches RTS around each frame with an exact timing.

- ``--rs485-rts-tx {high,low}``: RTS level while sending;
- ``--rs485-rts-rx {high,low}``: RTS level while receiving;
- ``--rs485-delay-before-tx S`` and ``--rs485-delay-before-rx S``: delays around a frame, 0 to 0.1 s (default: the
  value of the driver).

The RTS levels are logical: ``high`` is RTS asserted, which is a low voltage on the nRTS pin of most UARTs. A level
not given on the command line keeps the one of the driver when its RS-485 mode is already set (like by the
``rs485-rts-active-low`` property of the device tree): ``--rs485`` alone respects the configuration of the board.
Otherwise, the default is RTS high to send and low to receive (the only levels accepted on Windows). The log shows
the levels used, with ``(driver)`` for a level kept from the driver.

.. code-block:: bash

    # a board whose transceiver sends while RTS is not asserted (nRTS pin wired directly to the DE input)
    mbtcp-tcp2rtu --rs485 --rs485-rts-tx low --rs485-rts-rx high /dev/ttyRS485

The mode is applied again when a lost port is reopened. A driver without RS-485 support (a pseudo-terminal, most USB
adapters) refuses it at startup: the gateway exits with the code 2. On Windows, only the default levels and delays are
accepted; macOS has no RS-485 mode. Without these options, the serial port is opened as before.

Output of mbtcp-client
----------------------

On a terminal, ``mbtcp-client`` prints a header and a table: the offset from the first address and the addresses
in decimal and hexadecimal, the registers in hexadecimal, unsigned, signed, binary and ASCII, the bits in a grid of
10 per row, the device identification objects with their names:

.. code-block:: none

    $ mbtcp-client -p 5020 read-holding 10 -n 3
    read-holding · localhost:5020 · unit 1 · 3 holding registers @ 10..12 · 2.8 ms

      Offset  Address  Addr hex     Hex  Unsigned  Signed  Binary               ASCII
      ──────  ───────  ────────  ──────  ────────  ──────  ───────────────────  ─────
           0       10    0x000A  0x0001         1       1  0000 0000 0000 0001  ..
           1       11    0x000B  0xFFFE     65534      -2  1111 1111 1111 1110  ..
           2       12    0x000C  0x4142     16706   16706  0100 0001 0100 0010  AB

    $ mbtcp-client -p 5020 read-coils 100 -n 15
    read-coils · localhost:5020 · unit 1 · 15 coils @ 100..114 · 4.6 ms

      Offset  Address  Addr hex  +0 +1 +2 +3 +4  +5 +6 +7 +8 +9
      ──────  ───────  ────────  ──────────────────────────────
           0      100    0x0064   1  0  1  1  0   0  0  0  0  0
          10      110    0x006E   0  0  0  0  1

      4 on, 11 off

For ``write-read`` (function 0x17), the table has a ``Written`` column with the value written at each address, and
``!`` marks a register read with another value than the one written (changed by the server in the same request).

With ``-i``, the table is refreshed in place (like ``watch``) and the values that changed are highlighted (and
marked ``*``) for a few seconds. ``--no-color`` (or the ``NO_COLOR`` environment variable) turns the colors off.

``-f/--format`` chooses another output, for a script:

- ``plain``: one ``address value`` line per item (``--hex`` and ``--signed`` choose the notation of the registers);
- ``csv``: ``timestamp,address,value`` lines, after a header line;
- ``json``: one JSON object per read (JSON Lines with ``-i``);
- ``auto`` (default): ``table`` on a terminal, ``plain`` otherwise (a pipe, a file), so the output of a script never
  changes.

.. code-block:: bash

    # registers 0 to 9, every 5 s, in a CSV file
    mbtcp-client -H 192.168.1.10 read-holding 0 -n 10 -i 5 -f csv > log.csv

Values on several registers
---------------------------

The Modbus specification only defines 16 bits registers, sent big endian. A 32 or 64 bits value is stored on 2 or 4
consecutive registers, in an order that each device chooses: there is no standard, see the documentation of the
device. ``read-holding``, ``read-input-regs``, ``write-registers`` and ``write-read`` accept:

- ``-T/--type``: ``uint16`` (default), ``int16`` (1 register), ``uint32``, ``int32``, ``float32`` (2 registers),
  ``uint64``, ``int64``, ``float64`` (4 registers, IEEE 754 for the floats);
- ``-b/--byte-order``: the order of the bytes on the registers, in the notation of most Modbus software, ``A``
  being the most significant byte of the value.

==========  ===============================  ===================
Order       Name                             float32 3.14
==========  ===============================  ===================
``ABCD``    big endian (default)             ``4048 F5C3``
``CDAB``    word swap (common on PLCs)       ``F5C3 4048``
``BADC``    byte swap                        ``4840 C3F5``
``DCBA``    little endian                    ``C3F5 4840``
==========  ===============================  ===================

For a 64 bits value, ``CDAB`` reverses the order of the 4 registers (``GH EF CD AB``). With ``-T``, ``-n`` counts
values (``-n 2 -T float32`` reads 4 registers) and the table shows each value with its bits in hexadecimal (in the
``ABCD`` order) and its registers as read: to find the order of a device, read a known value with each order.

.. code-block:: none

    $ mbtcp-client -p 5020 read-holding 20 -n 2 -T float32 -b CDAB
    read-holding · localhost:5020 · unit 1 · 2 float32 CDAB @ 20..23 · 1.9 ms

      Offset  Address  Addr hex  Value         Hex  Registers
      ──────  ───────  ────────  ─────  ──────────  ─────────
           0       20    0x0014   3.14  0x4048F5C3  F5C3 4048
           2       22    0x0016   -2.5  0xC0200000  0000 C020

The ``plain`` and ``csv`` formats give the address of the first register of each value, ``--hex`` the bits of the
value; the ``json`` format adds the type and the byte order (NaN and infinities are the strings ``"nan"``,
``"inf"`` and ``"-inf"``). A negative value in exponent notation (``-1e-3``), ``-inf`` or ``-nan`` looks like an
option: write the values after ``--`` (``write-registers 0 -T float32 -- -1e-3``).

All the tools print their version (the one of pyModbusTCP) with ``-v/--version``.

The exit status of ``mbtcp-client`` is 0 on success, 1 if the request failed, 2 for an invalid parameter and 130 for
a request interrupted by Ctrl-C (Ctrl-C is the normal end of a read repeated with ``-i``: 0): it can be used in a
shell script.

Security
--------

Modbus/TCP has no authentication and no encryption: any host that can reach a server can read and write its data.
The server and the TCP to RTU gateway are designed to be safe by default:

- they listen on ``localhost`` and on port 5020 by default: use ``-H`` with the address of the network interface
  that needs it (``0.0.0.0`` only if every network of the host is trusted);
- ``-A/--allow`` restricts the clients to some IP addresses or networks, ``--read-only`` refuses every write;
- the TCP to RTU gateway refuses broadcast requests (unit id 0) unless ``--allow-broadcast`` is set (and then only
  for the write functions);
- ``--max-connections-per-host`` (default 16) keeps a single client host from taking every connection, and the
  TCP to RTU gateway shares the serial line between the client hosts: their requests are served in turn (round
  robin), with at most 2 pending requests per host;
- the TCP to RTU gateway never writes a request with an invalid function code (0, or 0x80 to 0xFF) on the bus;
- the delays (``-t``, ``-e``, ``-i``...) must be greater than 0 and at most one day, the serial rate greater than 0.

See also :doc:`../security`.

Port 502 without root
---------------------

.. include:: ../../examples/README.md
   :parser: myst_parser.sphinx_
   :start-after: ## Listen on the standard Modbus port 502 without root
   :end-before: ### In every case

.. note::

    This does not work with pipx, whose environments use links to the system interpreter (``setcap`` refuses them):
    install pyModbusTCP-cli in a dedicated virtual environment created with ``--copies``, then run
    ``venv/bin/mbtcp-server`` (the commands of the environment run its ``bin/python3``).

Options
-------

The ``--help`` of each tool, as of this version. For the commands of ``mbtcp-client``, run
``mbtcp-client COMMAND --help`` (like ``mbtcp-client read-holding --help``).

mbtcp-server
~~~~~~~~~~~~

.. literalinclude:: ../_generated/help_server.txt
   :language: none

mbtcp-client
~~~~~~~~~~~~

.. literalinclude:: ../_generated/help_client.txt
   :language: none

mbtcp-tcp2rtu
~~~~~~~~~~~~~

.. literalinclude:: ../_generated/help_tcp2rtu.txt
   :language: none

mbtcp-rtu2tcp
~~~~~~~~~~~~~

.. literalinclude:: ../_generated/help_rtu2tcp.txt
   :language: none
