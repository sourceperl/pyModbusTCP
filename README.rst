pyModbusTCP
===========

A simple Modbus/TCP client library for Python.

Since version 0.1.0, a server is also available for test purpose only (don't use in project).

pyModbusTCP is pure Python code without any extension or external module
dependency.

Test
----

The module is currently test on Python 2.6, 2.7, 3.2, 3.3, 3.4 and 3.5.

Status:

.. image:: https://api.travis-ci.org/sourceperl/pyModbusTCP.svg?branch=master
  :target: http://travis-ci.org/sourceperl/pyModbusTCP

.. image:: https://readthedocs.org/projects/pymodbustcp/badge/?version=latest
  :target: http://pymodbustcp.readthedocs.io/en/latest/?badge=latest

Setup
-----

You can install this package from:

PyPI, the easy way:

::

    sudo pip install pyModbusTCP

GitHub:

::

    git clone https://github.com/sourceperl/pyModbusTCP.git
    cd pyModbusTCP
    sudo python setup.py install

Install the current devel-release:

::

    sudo pip install git+https://github.com/sourceperl/pyModbusTCP.git@devel

Usage example
-------------

See examples/ for full scripts.

include (for all samples)
~~~~~~~~~~~~~~~~~~~~~~~~~

::

    from pyModbusTCP.client import ModbusClient

module init (TCP always open)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    # TCP auto connect on first modbus request
    c = ModbusClient(host="localhost", port=502, auto_open=True)

module init (TCP open/close for each request)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    # TCP auto connect on modbus request, close after it
    c = ModbusClient(host="127.0.0.1", auto_open=True, auto_close=True)

module init (with accessor functions)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    c = ModbusClient()
    c.host("localhost")
    c.port(502)
    # managing TCP sessions with call to c.open()/c.close()
    c.open()

Read 2x 16 bits registers at modbus address 0 :
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    regs = c.read_holding_registers(0, 2)
    if regs:
        print(regs)
    else:
        print("read error")

Write value 44 and 55 to registers at modbus address 10 :
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    if c.write_multiple_registers(10, [44,55]):
        print("write ok")
    else:
        print("write error")

Documentation
-------------

Documentation available online at http://pymodbustcp.readthedocs.io/.
