pyModbusTCP
===========

A simple Modbus/TCP client library for Python (beta release).

pyModbusTCP is pure Python code without any extension or external module
dependency.

Test
----

The module is currently test on Python 2.6, 2.7, 3.2, 3.3 and 3.4.

Status:

.. image:: https://api.travis-ci.org/sourceperl/pyModbusTCP.svg?branch=master
  :target: http://travis-ci.org/sourceperl/pyModbusTCP

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

include and module init (for all samples)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    from pyModbusTCP.client import ModbusClient
    c = ModbusClient()
    c.host("localhost")
    c.port(502)
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

Documentation available online at http://pythonhosted.org/pyModbusTCP and on
doc/html/index.html.
