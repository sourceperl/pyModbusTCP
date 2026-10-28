pyModbusTCP |release|
=====================

A simple Modbus/TCP client and server library for Python.

pyModbusTCP is pure Python code, without any extension or dependency outside of the standard library. It runs on
Python 3.10 to 3.14, on Linux, macOS and Windows.

- :doc:`ModbusClient <guide/client>`: read and write the data of a Modbus/TCP server (PLC, gateway, smart meter...).
- :doc:`ModbusServer <guide/server>`: expose the data of an application to Modbus/TCP clients (SCADA, HMI...).
- :doc:`Codec and utils <guide/utils>`: convert Modbus registers to and from 32/64 bits integers, IEEE floats, signed
  values, in any byte order.
- :doc:`Command line tools <guide/tools>`: a server, a client, two Modbus TCP/RTU gateways and an RTU scanner (finds
  the serial settings and the address of RTU slaves), ready to use.

.. code-block:: python

    from pyModbusTCP.client import ModbusClient

    c = ModbusClient(host='192.168.1.10', port=502, unit_id=1)
    regs = c.read_holding_registers(0, 10)
    print(regs if regs else 'read error: %s' % c.last_error_as_txt)

.. warning::

    Modbus/TCP has no authentication and no encryption: never expose a Modbus/TCP server on the Internet or on an
    untrusted network. See :doc:`security`.

.. toctree::
   :maxdepth: 1
   :caption: User guide

   quickstart/index
   guide/client
   guide/server
   guide/utils
   guide/tools

.. toctree::
   :maxdepth: 1
   :caption: Reference

   package/index
   examples/index

.. toctree::
   :maxdepth: 1
   :caption: Project

   security
   changelog
   GitHub repository <https://github.com/sourceperl/pyModbusTCP>
   PyPI package <https://pypi.org/project/pyModbusTCP/>
