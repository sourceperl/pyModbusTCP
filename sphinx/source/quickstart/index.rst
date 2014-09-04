Quickstart
==========

Overview of the package
-----------------------

pyModbusTCP give access to modbus/TCP server through the ModbusClient object: 
this class is define in the client module.

To deal with frequent need of modbus data mangling (for example 32 bits IEEE 
float to 2x16 bits words convertion) a special module named utils provide some 
helful functions.

**Package map:**

.. image:: map.png
   :scale: 75 %

Package setup
-------------

from PyPi::

    # for Python 2.7
    sudo pip-2.7 install pyModbusTCP
    # or for Python 3.2
    sudo pip-3.2 install pyModbusTCP
    # or upgrade from an older release
    sudo pip-3.2 install pyModbusTCP --upgrade

from Github::

    git clone https://github.com/sourceperl/pyModbusTCP.git
    cd pyModbusTCP
    # here change "python" by your python target(s) version(s) (like python3.2)
    sudo python setup.py install

init object ModbusClient
------------------------

init module from constructor (raise ValueError if host/port error)::

    from pyModbusTCP.client import ModbusClient
    try:
        c = ModbusClient(host="localhost", port=502)
    except ValueError:
        print("Error with host or port params")

you can also init module from functions host/port return None if error::

    from pyModbusTCP.client import ModbusClient
    c = ModbusClient()
    if not c.host("localhost"):
        print("host error")
    if not c.port(502):
        print("port error")

ModbusClient manage TCP link
----------------------------

Before each request, the TCP link need to be manualy open::

    if c.open():
        regs_list_1 = c.read_holding_registers(0, 10)
        regs_list_2 = c.read_holding_registers(55, 10)
        c.close()

In a loop::

    while True:
        if c.is_open():
            regs_list_1 = c.read_holding_registers(0, 10)
            regs_list_2 = c.read_holding_registers(55, 10)
        else:
            c.open()
        time.sleep(1)

ModbusClient available functions table
--------------------------------------

See http://en.wikipedia.org/wiki/Modbus for full table.

==============================  =============  =====================
        Function name           Function code  ModbusClient function
==============================  =============  =====================
Read Discrete Inputs                 2         read_discrete_inputs
Read Coils                           1         read_coils
Write Single Coil                    5         write_single_coil
Write Multiple Coils                15         n/a
Read Input Registers                 4         read_input_registers
Read Holding Registers               3         read_holding_registers
Write Single Register                6         write_single_register
Write Multiple Registers            16         write_multiple_registers
Read/Write Multiple Registers       23         n/a
Mask Write Register                 22         n/a
Read FIFO Queue                     24         n/a
Read File Record                    20         n/a
Write File Record                   21         n/a
Read Exception Status                7         n/a
Diagnostic                           8         n/a
Get Com Event Counter               11         n/a
Get Com Event Log                   12         n/a
Report Slave ID                     17         n/a
Read Device Identification          43         n/a
==============================  =============  =====================

Modbus data mangling
--------------------

*Coming soon.*

