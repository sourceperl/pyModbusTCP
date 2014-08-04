pyModbusTCP
===========

A simple Modbus/TCP library for Python (beta release, use only for test).

## Setup (for Linux):

1. clone repository

    pi@raspberrypi ~ $ git clone https://github.com/sourceperl/pyModbusTCP.git
    pi@raspberrypi ~ $ cd pyModbusTCP

2. launch install with sudo

    pi@raspberrypi ~ $ sudo python setup.py install

## Usage example

See examples/ for full scripts.

### include and module init (for all samples)

    from pyModbusTCP.client import ModbusClient
    c = ModbusClient()
    c.host("localhost")
    c.port(502)
    c.open()

### Read 2x 16 bits register at modbus address 0 :

    print c.read_holding_registers(0, 2)

### Write value 44 and 55 to register at modbus address 10 :

    if c.write_multiple_registers(10, [44,55]):
        print("write ok !")

