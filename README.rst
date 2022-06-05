.. |badge_tests| image:: https://github.com/sourceperl/pyModbusTCP/actions/workflows/tests.yml/badge.svg?branch=master
                :target: https://github.com/sourceperl/pyModbusTCP/actions/workflows/tests.yml

.. |badge_docs| image:: https://readthedocs.org/projects/pymodbustcp/badge/?version=latest
               :target: http://pymodbustcp.readthedocs.io/

pyModbusTCP |badge_tests| |badge_docs|
======================================

A simple Modbus/TCP client library for Python.
pyModbusTCP is pure Python code without any extension or external module dependency.

Since version 0.1.0, a server is also available for test purpose only (don't use in project).

Tests
-----

The module is currently test on Python 3.5, 3.6, 3.7, 3.8, 3.9 and 3.10.

For Linux, Mac OS and Windows.

Documentation
-------------

Documentation of the last release is available online at https://pymodbustcp.readthedocs.io/.

Setup
-----

You can install this package from:

PyPI, the easy way:

.. code-block:: bash

    # install the last available release (stable)
    sudo pip install pyModbusTCP

.. code-block:: bash

    # install a specific version (here release v0.1.10)
    sudo pip install pyModbusTCP==v0.1.10

From GitHub:

.. code-block:: bash

    # install a specific version (here release v0.1.10) directly from github servers
    sudo pip install git+https://github.com/sourceperl/pyModbusTCP.git@v0.1.10

Note on the use of versions:

Over time, some things can change. So, it's a good practice that you always use a specific version of a package for
your project, instead of just relying on the default behavior. Without precision, the installation tools will always
install the latest version available for a package, this may have some drawbacks. For example, in pyModbusTCP, the TCP
automatic open mode will be active by default from version 0.2.0. It is not the case with previous versions and it just
doesn't exist before the 0.0.12. This can lead to some strange behaviour of your application if you are not aware of
the change. Look at `CHANGES <https://github.com/sourceperl/pyModbusTCP/blob/master/CHANGES>`_ for details on versions
available.

Usage example
-------------

See examples/ for full scripts.

include (for all samples)
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from pyModbusTCP.client import ModbusClient

module init (TCP always open)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # TCP auto connect on first modbus request
    c = ModbusClient(host="localhost", port=502, unit_id=1, auto_open=True)

module init (TCP open/close for each request)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    # TCP auto connect on modbus request, close after it
    c = ModbusClient(host="127.0.0.1", auto_open=True, auto_close=True)

Read 2x 16 bits registers at modbus address 0 :
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    regs = c.read_holding_registers(0, 2)

    if regs:
        print(regs)
    else:
        print("read error")

Write value 44 and 55 to registers at modbus address 10 :
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    if c.write_multiple_registers(10, [44,55]):
        print("write ok")
    else:
        print("write error")
