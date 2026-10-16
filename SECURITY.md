# Security

This document describes what pyModbusTCP does (and does not do) to protect an application, how to deploy it
safely, and the limitations you should know about.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use the private vulnerability reporting of GitHub
(the "Security" tab of the repository, then "Report a vulnerability"), with a description of the problem and,
if possible, a way to reproduce it.

## Threat model: Modbus/TCP has no security

Modbus/TCP was designed for closed industrial networks. The protocol itself has:

- **no authentication**: any host that can open a TCP connection to the server can read and write its data;
- **no encryption**: requests and replies (values, set points, commands) travel in clear text;
- **no integrity protection**: a frame can be modified on the way without being detected.

pyModbusTCP implements this protocol as it is: it can't add what the protocol lacks. The security of a
Modbus/TCP installation relies first on the network around it.

The secure variant of the protocol (Modbus/TCP Security, TLS with mutual authentication on port 802) is not
supported.

## Deployment recommendations

- **Never expose a Modbus/TCP port on the Internet.** Keep it on an isolated network (dedicated VLAN, firewall
  between the IT and OT networks), and reach it from outside only through a VPN.
- **Filter at the network level** (firewall rules on port 502): it's the only filter that can't be bypassed by
  an attacker on the same network segment.
- **Listen on the right interface.** The server default host is `localhost`: only listen on `0.0.0.0` (or `::`)
  if every interface of the host is trusted, prefer the address of the interface that needs it.
- **Don't run the server as root** to listen on port 502 (a privileged port on Unix systems). Use a port above
  1023 with a redirection on the firewall, or give the capability `CAP_NET_BIND_SERVICE` to the Python
  interpreter of a dedicated virtual environment.
- **Install the package in a virtual environment** (`python -m venv`), not with `sudo pip install`, and pin its
  version.
- **Limit the published device identification** (function 0x2B/0x0E): every object set in
  `DeviceIdentification` (vendor, product, revision, URL...) can be read by any client and helps an attacker to
  look for known flaws of a device.

## Server protections

### Access control

| Option | Default | Effect |
|---|---|---|
| `host` | `'localhost'` | Interface the server listens on. |
| `allowed_hosts` | `None` (every client) | Whitelist of IP addresses or networks (CIDR notation, IPv4 and IPv6). Other clients are disconnected at once, before any engine (internal or external) is called. Refusals are logged (one message per 10 s at most). |
| `read_only` | `False` | Every request that writes data (functions 0x05, 0x06, 0x0F, 0x10 and 0x17) gets an "illegal function" exception. |

`allowed_hosts` is a filter on the source IP address, **not an authentication**: on a shared network segment
an attacker can spoof an allowed address (ARP spoofing for example). Use it as a second line of defense behind
a firewall.

### Resource limits

| Option | Default | Effect |
|---|---|---|
| `max_connections` | `64` | Max number of simultaneous client connections, the next ones are closed at once (logged, one message per 10 s at most). |
| `max_connections_per_host` | `16` | Max number of simultaneous connections of a client host (an IPv4 address, or a /64 network for IPv6): a single host can't take every connection of `max_connections`. The next ones are closed at once (logged, one message per 10 s at most). Clients behind a NAT share the same address. |
| `idle_timeout` | `300` s | A connection without any request for this time is closed. |
| `request_timeout` | `30` s | A request that has started must be fully received in this time (protection against a client that sends a partial frame and then stays silent). |

Each of these limits can be disabled with `None`: only do this on a trusted network.

These limits make a denial of service harder, they don't prevent it: a few hosts can still take every connection
(`max_connections` / `max_connections_per_host` hosts), and keep them with one request per `idle_timeout`. On a
network where some hosts are not trusted, `allowed_hosts` and the firewall are the real protections.

### TCP to RTU gateway (`mbtcp-tcp2rtu`)

The serial line serves one request at a time, and a request to a slave that doesn't answer holds it for the whole
timeout. A client host has at most 2 requests waiting for the serial line: its next ones get the exception 0x0A
(gateway path unavailable) at once, and the requests of the other hosts still reach the line. The gateway also has
the `--max-connections` and `--max-connections-per-host` options of the server. Requests refused by its filters
are logged once, then once per 10 s at most, so a request flood can't flood the log.

### Listening socket

On Windows, the listening port is reserved with `SO_EXCLUSIVEADDRUSE`: no other socket (of the same process, of
another process or of another user account) can bind it while the server runs and take its connections (port
hijacking). The `SO_REUSEADDR` option, which allows this hijacking on Windows, is only set on the other systems,
where it just allows a quick restart of the server on the same port.

On Windows, a server restarted on the same port may get an "address already in use" error for a short time, until
the connections of the previous one are fully closed by the system.

### Frame checks

- The MBAP header is checked (protocol ID must be 0, length from 2 to 254 bytes): an invalid header closes
  the connection, since the TCP stream can no longer be trusted.
- The PDU of a request is checked against its function: quantities in the ranges of the specification, a byte
  count that matches the quantity for the write functions, no missing data. An invalid request gets an
  "illegal data value" exception and the session stays in sync. Bytes after a complete and consistent request
  are ignored.
- An exception raised by user code (data handler, `on_coils_change()`, `on_holding_registers_change()`...) is
  logged and answered with a "slave device failure" exception, the session is kept. An exception raised by an
  external engine (`ext_engine`) closes the session.

## Client protections

- `timeout` is a limit for the whole request (send and receive), not for each socket operation: a server that
  sends its reply byte by byte can't hold the client longer than `timeout`.
- The reply is checked before use: MBAP header (transaction ID, protocol ID, length, unit ID), function code
  of the request, byte counts, and the echo of the address and value/quantity for the write functions.
  An invalid MBAP header closes the connection.
- The transaction ID is a 16 bits counter (from a random start value): two consecutive requests never share
  the same ID, so a late reply to a previous request is never taken for the current one.
- Parameters are checked before any network I/O (types, ranges, end of the address space).

## Known limitations

These points are not vulnerabilities of the code, but behaviors to take into account in the design of an
application.

- **`read_only` has no effect with an external engine** (`ext_engine`): an external engine handles every
  request itself and must refuse the writes on its own.
- **`ModbusClient` is not thread-safe**: share an instance between threads only with an external lock.
- **Client name resolution** is not limited by `timeout`, and the connection timeout applies to each address
  returned by the resolver in turn.
