# pyModbusTCP-cli

Command line tools built on [pyModbusTCP](https://github.com/sourceperl/pyModbusTCP):

| Command          | Role                                                                       |
|------------------|----------------------------------------------------------------------------|
| `mbtcp-server`   | Modbus/TCP server (test a client, a SCADA, a gateway...)                   |
| `mbtcp-client`   | Modbus/TCP client: read and write requests from the command line           |
| `mbtcp-tcp2rtu`  | gateway: Modbus/TCP clients -> Modbus/RTU slaves on a serial line          |
| `mbtcp-rtu2tcp`  | gateway: a Modbus/RTU master on a serial line -> a Modbus/TCP server       |

The code of the tools is in the pyModbusTCP library (module `pyModbusTCP.tools`). This package contains no code:
it only adds the commands to the PATH, with pyserial for the gateways. So a plain `pip install pyModbusTCP` puts no
command in the PATH and installs no dependency.

## Install

```bash
# as isolated command line tools (recommended)
pipx install pyModbusTCP-cli

# or in a virtual environment, with the library
pip install "pyModbusTCP[cli]==0.4.0a1"
```

**Alpha**: pyModbusTCP-cli is new with pyModbusTCP 0.4.0, only pre-releases exist for now. The commands, their
options and their output may still change before the final release. Without a version or `--pre`,
`pip install "pyModbusTCP[cli]"` installs the stable pyModbusTCP 0.3.1, without the tools.

Without this package, the tools can still be run from the library alone:

```bash
python -m pyModbusTCP.tools server --help
python -m pyModbusTCP.tools client --help
pip install "pyModbusTCP[serial]==0.4.0a1"  # pyserial, only needed by the gateways
python -m pyModbusTCP.tools tcp2rtu --help
python -m pyModbusTCP.tools rtu2tcp --help
```

## Examples

```bash
# server on localhost:5020, log the writes of the clients
mbtcp-server --log-changes

# read 4 holding registers from address 0, write some values
mbtcp-client -p 5020 read-holding 0 -n 4
mbtcp-client -p 5020 write-registers 0 100 200 0x1234
mbtcp-client -p 5020 write-coil 10 on

# TCP clients of the 192.168.1.0/24 network can read (only) the RTU slaves of /dev/ttyUSB0
mbtcp-tcp2rtu -H 0.0.0.0 -A 192.168.1.0/24 --read-only -b 19200 --parity E /dev/ttyUSB0

# an RTU master on /dev/ttyUSB0 reads the Modbus/TCP server 192.168.1.10 (the gateway is the RTU slave 30)
mbtcp-rtu2tcp -a 30 -H 192.168.1.10 /dev/ttyUSB0
```

Run any command with `--help` for all its options.

## Security

Modbus/TCP has no authentication and no encryption: any host that can reach a server can read and write its data.
The server and the TCP to RTU gateway are designed to be safe by default:

- they listen on `localhost` and on port 5020 by default: use `-H` with the address of the network interface that
  needs it (`0.0.0.0` only if every network of the host is trusted);
- `-A/--allow` restricts the clients to some IP addresses or networks, `--read-only` refuses every write;
- the TCP to RTU gateway refuses broadcast requests (unit id 0) unless `--allow-broadcast` is set;
- `--max-connections-per-host` (default 16) keeps a single client host from taking every connection, and the TCP
  to RTU gateway shares the serial line between the client hosts (at most 2 pending requests per host).

Port 502 (the standard Modbus/TCP port) is a privileged port on Unix systems. **Never run these commands as root (or
with sudo) to listen on it.** Give the `CAP_NET_BIND_SERVICE` capability to the Python interpreter of a virtual
environment dedicated to the tools instead:

```bash
python3 -m venv --copies ~/mbtcp-venv          # --copies: real interpreter files, not links to the system one
~/mbtcp-venv/bin/pip install pyModbusTCP-cli
sudo setcap 'cap_net_bind_service=+ep' ~/mbtcp-venv/bin/python3
~/mbtcp-venv/bin/mbtcp-server -H 192.168.1.10 -p 502
```

The commands of this virtual environment run its `bin/python3`, the file with the capability. This does not work
with pipx, whose environments use links to the system interpreter (`setcap` refuses them). See "Listen on the standard
Modbus port 502 without root" in the [examples README](https://github.com/sourceperl/pyModbusTCP/tree/master/examples)
for the details and the precautions.

See also the [security policy](https://github.com/sourceperl/pyModbusTCP/blob/master/SECURITY.md) of pyModbusTCP.

## Versions

pyModbusTCP-cli has the version of pyModbusTCP and requires exactly this release of the library (where its code
is).
