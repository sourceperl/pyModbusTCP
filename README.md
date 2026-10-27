# pyModbusTCP [![Tests](https://github.com/sourceperl/pyModbusTCP/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/sourceperl/pyModbusTCP/actions/workflows/tests.yml)

A simple Modbus/TCP client and server library for Python.

pyModbusTCP is pure Python code without any extension or external module dependency.

## Tests

The module is currently tested on Python 3.10, 3.11, 3.12, 3.13 and 3.14, on Linux, macOS and Windows.

## Documentation

Documentation of the latest release is available online at https://pymodbustcp.readthedocs.io/.

## Security

Modbus/TCP has no authentication and no encryption: never expose a Modbus/TCP server on the Internet or on an
untrusted network. See [SECURITY](https://github.com/sourceperl/pyModbusTCP/blob/master/SECURITY.md) for the
protections of the client and the server, the deployment recommendations, the known limitations and how to report
a vulnerability.

## Setup

Install the package in a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

From PyPI, the easy way:

```bash
# install the latest available release (stable)
pip install pyModbusTCP
```

```bash
# install a specific version (here release 0.3.1)
pip install pyModbusTCP==0.3.1
```

Pre-release (alpha) of the next version 0.4.0, to test it before the final release: the command line tools, their
options and their output may still change. pip never installs a pre-release by default, ask for it explicitly:

```bash
# a specific pre-release
pip install pyModbusTCP==0.4.0a3
# or the latest release, pre-releases included
pip install --pre pyModbusTCP
```

From GitHub:

```bash
# install a specific version (here release 0.3.1) directly from github servers
pip install git+https://github.com/sourceperl/pyModbusTCP.git@v0.3.1
```

Note on the use of versions:

Over time, some things can change. So, it's a good practice to always use a specific version of a package for your
project, instead of just relying on the default behavior: without it, the installation tools always install the latest
available version of a package. For example, in pyModbusTCP, the automatic TCP open mode (`auto_open`) is on by
default since version 0.2.0, it was off in the previous versions and doesn't exist before version 0.0.12. This can lead
to some strange behavior of your application if you are not aware of the change. Look at
[CHANGES](https://github.com/sourceperl/pyModbusTCP/blob/master/CHANGES) for details on the available versions.

## Usage example

See [examples/](https://github.com/sourceperl/pyModbusTCP/tree/master/examples) for full scripts.

### Client

```python
from pyModbusTCP.client import ModbusClient

# TCP connection is opened at the first request (auto_open is on by default) and kept open
c = ModbusClient(host='localhost', port=502, unit_id=1)

# or: TCP connection opened before each request and closed after it
c = ModbusClient(host='127.0.0.1', auto_close=True)
```

Read 2x 16 bits registers at modbus address 0:

```python
regs = c.read_holding_registers(0, 2)

if regs:
    print(regs)
else:
    print('read error: %s' % c.last_error_as_txt)
```

Write values 44 and 55 to registers at modbus address 10:

```python
if c.write_multiple_registers(10, [44, 55]):
    print('write ok')
else:
    print('write error: %s' % c.last_error_as_txt)
```

Use the client as a context manager (the TCP connection is closed at the end of the block):

```python
with ModbusClient(host='localhost', port=502) as c:
    print(c.read_coils(0, 8))
```

### Server

```python
from pyModbusTCP.server import ModbusServer

# listen on localhost only (the default host), port 502 needs root privileges on Unix systems: use another one
server = ModbusServer(host='localhost', port=5020, no_block=True)
server.start()

# update the data seen by the clients from the application
server.store.app.holding_registers.write(0, [1, 2, 3])
```

A server open to the network, with some access restrictions (see
[SECURITY](https://github.com/sourceperl/pyModbusTCP/blob/master/SECURITY.md)):

```python
# only the hosts of the 192.168.1.0/24 network can connect, every write request is refused
server = ModbusServer(host='0.0.0.0', port=5020, allowed_hosts=['192.168.1.0/24'], read_only=True)
server.start()
```

## Command line tools

***The tools are new in 0.4.0 (only pre-releases for now): without a version or `--pre`,
`pip install "pyModbusTCP[cli]"` installs the stable 0.3.1, without the tools (pip only shows a warning).***

The library comes with command line tools: a Modbus/TCP server, a Modbus/TCP client, two gateways between
Modbus/TCP and Modbus/RTU (serial line), and a scanner that finds the serial settings and the address of RTU slaves.
They run from the library itself:

```bash
python -m pyModbusTCP.tools server --help
python -m pyModbusTCP.tools client -H 192.168.1.10 read-holding 0 -n 4

# the serial tools need pyserial (an optional dependency)
pip install "pyModbusTCP[serial]"
python -m pyModbusTCP.tools tcp2rtu --help
python -m pyModbusTCP.tools rtu2tcp --help
python -m pyModbusTCP.tools rtu-scan --help
```

To have them as commands in the PATH (`mbtcp-server`, `mbtcp-client`, `mbtcp-tcp2rtu`, `mbtcp-rtu2tcp` and
`mbtcp-rtu-scan`),
install the optional package pyModbusTCP-cli. A plain `pip install pyModbusTCP` puts no command in the PATH.

```bash
# in a virtual environment, with the library
pip install "pyModbusTCP[cli]==0.4.0a3"
# or as isolated command line tools
pipx install pyModbusTCP-cli
```
