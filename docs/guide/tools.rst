Command line tools
==================

pyModbusTCP comes with 4 command line tools, built on the library:

.. list-table::
   :header-rows: 1
   :widths: 22 33 45

   * - Command
     - Without pyModbusTCP-tools
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
pyModbusTCP-tools package (it contains no code, only the commands and pyserial):

.. code-block:: bash

    # as isolated command line tools (recommended)
    pipx install pyModbusTCP-tools

    # or in a virtual environment, with the library
    pip install "pyModbusTCP[tools]"

pyModbusTCP-tools always has the version of the library it requires.

.. note::

    The tools are new in version 0.4.0, only available as a pre-release (alpha) for now: their options and their
    output may still change. ``pip install "pyModbusTCP[tools]"`` without a version installs the stable 0.3.1,
    without the tools: use ``pip install "pyModbusTCP[tools]==0.4.0a1"`` (or ``--pre``).

Examples
--------

.. code-block:: bash

    # server on localhost:5020, log the writes of the clients
    mbtcp-server --log-changes

    # read 4 holding registers from address 0, write some values
    mbtcp-client -p 5020 read-holding 0 -n 4
    mbtcp-client -p 5020 write-registers 0 100 200 0x1234
    mbtcp-client -p 5020 write-coil 10 on

    # read a device every second, show the frames
    mbtcp-client -H 192.168.1.10 -d read-input-regs 0 -n 2 -i 1

    # TCP clients of the 192.168.1.0/24 network can read (only) the RTU slaves of /dev/ttyUSB0
    mbtcp-tcp2rtu -H 0.0.0.0 -A 192.168.1.0/24 --read-only -b 19200 --parity E /dev/ttyUSB0

    # a RTU master on /dev/ttyUSB0 reads the Modbus/TCP server 192.168.1.10 (the gateway is the RTU slave 30)
    mbtcp-rtu2tcp -a 30 -H 192.168.1.10 /dev/ttyUSB0

The exit status of ``mbtcp-client`` is 0 on success, 1 if the request failed and 2 for an invalid parameter: it can
be used in a shell script.

Security
--------

Modbus/TCP has no authentication and no encryption: any host that can reach a server can read and write its data.
The server and the TCP to RTU gateway are designed to be safe by default:

- they listen on ``localhost`` and on port 5020 by default: use ``-H`` with the address of the network interface
  that needs it (``0.0.0.0`` only if every network of the host is trusted);
- ``-A/--allow`` restricts the clients to some IP addresses or networks, ``--read-only`` refuses every write;
- the TCP to RTU gateway refuses broadcast requests (unit id 0) unless ``--allow-broadcast`` is set.

See also :doc:`../security`.

Port 502 without root
---------------------

.. include:: ../../examples/README.md
   :parser: myst_parser.sphinx_
   :start-after: ## Listen on the standard Modbus port 502 without root
   :end-before: ### In every case

.. note::

    This does not work with pipx, whose environments use links to the system interpreter (``setcap`` refuses them):
    install pyModbusTCP-tools in a dedicated virtual environment created with ``--copies``, then run
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
