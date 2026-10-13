.. |badge_tests| image:: https://github.com/sourceperl/pyModbusTCP/actions/workflows/tests.yml/badge.svg?branch=master
                :target: https://github.com/sourceperl/pyModbusTCP/actions/workflows/tests.yml

.. |badge_docs| image:: https://readthedocs.org/projects/pymodbustcp/badge/?version=latest
               :target: https://pymodbustcp.readthedocs.io/

pyModbusTCP |badge_tests| |badge_docs|
======================================

A simple Modbus/TCP client and server library for Python.

pyModbusTCP is pure Python code without any extension or external module dependency.

Tests
-----

The module is currently tested on Python 3.10, 3.11, 3.12, 3.13 and 3.14, on Linux, macOS and Windows.

Documentation
-------------

Documentation of the last release is available online at https://pymodbustcp.readthedocs.io/.

Security
--------

Modbus/TCP has no authentication and no encryption: never expose a Modbus/TCP server on the Internet or on an
untrusted network. See `SECURITY <https://github.com/sourceperl/pyModbusTCP/blob/master/SECURITY.md>`_ for the
protections of the client and the server, the deployment recommendations, the known limitations and how to report
a vulnerability.

Setup
-----

Install the package in a virtual environment:

.. code-block:: bash

    python3 -m venv venv
    source venv/bin/activate

From PyPI, the easy way:

.. code-block:: bash

    # install the last available release (stable)
    pip install pyModbusTCP

.. code-block:: bash

    # install a specific version (here release 0.3.1)
    pip install pyModbusTCP==0.3.1

From GitHub:

.. code-block:: bash

    # install a specific version (here release 0.3.1) directly from github servers
    pip install git+https://github.com/sourceperl/pyModbusTCP.git@v0.3.1

Note on the use of versions:

Over time, some things can change. So, it's a good practice to always use a specific version of a package for your
project, instead of just relying on the default behavior: without it, the installation tools always install the latest
available version of a package. For example, in pyModbusTCP, the automatic TCP open mode (``auto_open``) is on by
default since version 0.2.0, it was off in the previous versions and doesn't exist before version 0.0.12. This can lead
to some strange behavior of your application if you are not aware of the change. Look at
`CHANGES <https://github.com/sourceperl/pyModbusTCP/blob/master/CHANGES>`_ for details on the available versions.

Usage example
-------------

See `examples/ <https://github.com/sourceperl/pyModbusTCP/tree/master/examples>`_ for full scripts.

Client
~~~~~~

.. code-block:: python

    from pyModbusTCP.client import ModbusClient

    # TCP connection is opened at the first request (auto_open is on by default) and kept open
    c = ModbusClient(host='localhost', port=502, unit_id=1)

    # or: TCP connection opened before each request and closed after it
    c = ModbusClient(host='127.0.0.1', auto_close=True)

Read 2x 16 bits registers at modbus address 0:

.. code-block:: python

    regs = c.read_holding_registers(0, 2)

    if regs:
        print(regs)
    else:
        print('read error: %s' % c.last_error_as_txt)

Write values 44 and 55 to registers at modbus address 10:

.. code-block:: python

    if c.write_multiple_registers(10, [44, 55]):
        print('write ok')
    else:
        print('write error: %s' % c.last_error_as_txt)

Use the client as a context manager (the TCP connection is closed at the end of the block):

.. code-block:: python

    with ModbusClient(host='localhost', port=502) as c:
        print(c.read_coils(0, 8))

Server
~~~~~~

.. code-block:: python

    from pyModbusTCP.server import ModbusServer

    # listen on localhost only (the default host), port 502 needs root privileges on Unix systems: use another one
    server = ModbusServer(host='localhost', port=5020, no_block=True)
    server.start()

    # update the data seen by the clients from the application
    server.data_bank.set_holding_registers(0, [1, 2, 3])

A server open to the network, with some access restrictions (see `SECURITY
<https://github.com/sourceperl/pyModbusTCP/blob/master/SECURITY.md>`_):

.. code-block:: python

    # only the hosts of the 192.168.1.0/24 network can connect, every write request is refused
    server = ModbusServer(host='0.0.0.0', port=5020, allowed_hosts=['192.168.1.0/24'], read_only=True)
    server.start()