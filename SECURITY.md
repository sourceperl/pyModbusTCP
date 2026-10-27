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
| `host` | `'localhost'` | Interface the server listens on. An empty host (the wildcard address of `bind()`: every interface) is refused: write `'0.0.0.0'` (or `'::'`) to listen on every interface on purpose. So is an IPv4 address in a legacy form that `bind()` reads as another address: `'0'`, `'00'` or `'0x0'` is 0.0.0.0 (every interface), `'192.168.010.1'` is 192.168.8.1 (octal), `'127.1'` is 127.0.0.1. |
| `allowed_hosts` | `None` (every client) | Whitelist of IP addresses or networks (CIDR notation, IPv4 and IPv6). Other clients are disconnected at once, before any function handler (or fallback) is called. Refusals are logged (one message per 10 s at most). An empty list allows no client at all (a warning is logged at startup). An IPv4-mapped entry (`::ffff:192.168.1.10`) is converted to IPv4, the form of such a client for the server; a link-local entry (`fe80::/10`), or an entry with an interface (`%eth0`), is refused (see "Link-local IPv6 addresses"). |
| `read_only` | `False` | The standard write functions (0x05, 0x06, 0x0F, 0x10, 0x15, 0x16 and 0x17) get an "illegal function" exception, whatever their handler. A fallback handler (like a gateway) only gets the read functions (0x01 to 0x04, 0x2B/0x0E): any other function may write. A function added or replaced by the application is not checked: if it writes data, it must test `server.read_only` (see the known limitations). For finer rules (some spaces, some clients), stack the `ReadOnly`, `AccessControl` or `Guard` wrappers on the data store. |

These options are also properties of the server: a new value is checked like the constructor argument and
applies at once to a running server. A new `allowed_hosts` filters the next connections and closes the active
sessions of the clients it no longer allows.

`allowed_hosts` is a filter on the source IP address, **not an authentication**: on a shared network segment
an attacker can spoof an allowed address (ARP spoofing for example). Use it as a second line of defense behind
a firewall.

### Resource limits

| Option | Default | Effect |
|---|---|---|
| `max_connections` | `64` | Max number of simultaneous client connections, the next ones are closed at once (logged, one message per 10 s at most). |
| `max_connections_per_host` | `16` | Max number of simultaneous connections of a client host (an IPv4 address, or an IPv6 network, see `ipv6_host_prefix`): a single host can't take every connection of `max_connections`. The next ones are closed at once (logged, one message per 10 s at most). Clients behind a NAT share the same address. |
| `ipv6_host_prefix` | `64` | Prefix length of the IPv6 network counted as one host by `max_connections_per_host` (and by the fair share of `mbtcp-tcp2rtu`). A host usually gets a whole /64 and can use any address of it (SLAAC, temporary addresses): a longer prefix is easy to bypass. `128` counts each address apart, only for a network where a host can't choose its addresses (static addresses, no SLAAC, source address filtering on the switches); a shorter one (like `56`) counts a client with a whole /56 as one host, but also groups distinct clients of this network. The clients of one IPv6 LAN share a /64, like the clients behind a NAT. |
| `idle_timeout` | `300` s | A connection without any request for this time is closed. |
| `request_timeout` | `30` s | A request that has started must be fully received in this time (protection against a client that sends a partial frame and then stays silent). |
| `MemoryStore.notify_queue_size` | `10000` | Max number of change events waiting for the threaded listeners (`subscribe(..., threaded=True)`): clients that write faster than a slow listener can't make the memory grow without limit. The events over it are dropped (logged, one message per 10 s at most). |

Each of these limits can be disabled with `None`: only do this on a trusted network.

These limits make a denial of service harder, they don't prevent it:

- a few hosts can still take every connection (`max_connections` / `max_connections_per_host` hosts, 4 with the
  defaults), and keep them with one request per `idle_timeout`;
- an IPv6 host is counted by /64 network (`ipv6_host_prefix`): a client with a larger prefix (a /56 or a /48, common
  for a customer of an ISP or a cloud provider) counts as many hosts;
- there is no limit on the rate of the requests in a connection (thousands of requests per second).

No setting of the server can tell an attacker that behaves like a client from a real one. The protection is on the
network: on a network where some hosts are not trusted, `allowed_hosts` and the firewall are the real protections.

#### Firewall rules against a denial of service

1. Allow only the known clients on the Modbus port: an attacker that can't reach it can't take its connections.
   With ufw:

   ```bash
   ufw default deny incoming
   ufw allow from 192.168.1.10 to any port 502 proto tcp      # the SCADA
   ufw allow from 192.168.1.0/28 to any port 502 proto tcp    # the engineering stations
   ```

2. Limit the simultaneous connections of each client (an allowed client that is compromised or faulty), by address
   for IPv4 and by prefix for IPv6 (`connlimit`, here 4 connections per address or per /56):

   ```bash
   iptables  -A INPUT -p tcp --syn --dport 502 -m connlimit --connlimit-above 4 --connlimit-mask 32 \
             -j REJECT --reject-with tcp-reset
   ip6tables -A INPUT -p tcp --syn --dport 502 -m connlimit --connlimit-above 4 --connlimit-mask 56 \
             -j REJECT --reject-with tcp-reset
   ```

   ufw has no command for `connlimit`: add these rules to `/etc/ufw/before.rules` and `/etc/ufw/before6.rules`, or
   write them with nftables (`ct count`).

`ufw limit` (and the other rate limits of new connections) is not a protection against this denial of service: it
counts the connections opened per 30 s, not the ones that stay open (an attacker takes its places in a few minutes,
then keeps them), it doesn't see the requests sent in a connection, and it counts an IPv6 client by address. It also
blocks a legitimate client that opens a connection per request (`ModbusClient(auto_close=True)` polling every 2 s
opens 15 connections per 30 s, ufw refuses more than 5).

The firewalls of Windows and macOS have no simple equivalent of `connlimit`: there, the whitelist (firewall rules or
`allowed_hosts`) is the main protection.

### TCP to RTU gateway (`mbtcp-tcp2rtu`)

The serial line serves one request at a time, and a request to a slave that doesn't answer holds it for the whole
timeout. The waiting requests are served in turn by client host (round robin, a host is an IPv4 address or a /64
network for IPv6, see `--ipv6-host-prefix`): a request waits for at most one request of each other host, so a few hosts that flood the gateway
can't take every turn of the line. A client host has at most 2 requests waiting for the serial line or in progress
on it: its next ones get the exception 0x0A (gateway path unavailable) at once, like any request once 16 requests are
waiting. The gateway also has the `--max-connections` and `--max-connections-per-host` options of the server.
Requests refused by its filters are logged once, then once per 10 s at most, so a request flood can't flood the log.

An RTU frame has no transaction id: the gateway checks that the answer of a slave matches its request (slave
address, function code, size of the data read, echo of a write) and ignores any other frame until the timeout: a
late reply of a slave is not taken for the response of another request. One case remains: a late reply with
exactly the shape of the next response to the same slave (same function, same quantity) can't be told apart. Set
the timeout (`-t`) above the response time of the slowest slave.

The gateway only relays the standard functions that read and write data by default (0x01 to 0x06, 0x0F, 0x10, 0x16,
0x17 and 0x2B/0x0E): the diagnostics 0x08 (restart of a slave, listen only mode that makes it silent until a power
cycle), the file records, the other MEI types of 0x2B and the vendor functions (programming, firmware) get the
exception 0x01 and never reach the serial line. `--functions` sets this whitelist (`--functions all`: every
function, with a warning at startup), `--read-only` is a shortcut for the read functions.

A request with an invalid function code (0, or 0x80 to 0xFF: the exception flag of a reply) gets the exception 0x01
(illegal function) and is never written on the serial line.

Broadcast requests (unit id 0, only with `--allow-broadcast`) have a small buffer: a few of them wait for the line
(`--broadcast-buffer`, 4 by default, 8 at most: half of the queue, the other requests keep their place), the next
ones get the exception 0x06 (slave device busy) at once. A broadcast that waited more than `--broadcast-max-age`
(10 s by default) is dropped, never written. A client that sends broadcasts faster than the line can take them never
builds a backlog of old writes, applied long after it stopped. The buffer is shared by all the client hosts: a host
that floods it delays the broadcasts of the others (0x06), never their other requests.

With N client hosts that send requests at the same time, each one gets 1/N of the serial line: many hosts can
still slow down the others. As for the server, `--allow` and the firewall are the real protections.

### Link-local IPv6 addresses

Every IPv6 interface has a link-local address (`fe80::/10`), only valid on its own link (never routed). Every
interface has one in the same `fe80::/64`: the interface tells which link is meant, so a link-local address is only
accepted with it, by the server and by the client (`'fe80::1%eth0'`, `'fe80::1%2'` with an interface index on
Windows). Without it the target is ambiguous: `ValueError`, with the form to write. An interface on another address
(`'2001:db8::1%eth0'`) is a configuration error: refused too.

`allowed_hosts` (and `AccessControl`, `-A/--allow`) can't tell the interface of a link-local client: the interface
is not part of an IP network, an entry like `fe80::/10` would allow the link-local clients of every interface. So a
link-local entry, or an entry with an interface (`fe80::1%eth0`), is refused (`ValueError`, exit status 2 for the
tools). A wider entry that contains `fe80::/10` (like `::/0`, every IPv6 client) is accepted: the link-local clients of
every interface are then allowed, on purpose. To accept the link-local clients of one link only:

- listen on the link-local address of this interface (`ModbusServer(host='fe80::1%eth1', ipv6=True)`): the other
  links can't reach the server;
- or filter by interface on the firewall (`ip6tables -A INPUT -i eth1 -p tcp --dport 502 -j ACCEPT`).

`max_connections_per_host` counts the link-local clients by `fe80::/64` too: two clients `fe80::1` of two links share
one quota.

Link-local addresses depend on the name or the index of an interface (renamed, or numbered in another order at
boot), and a router can't filter them: keep them for commissioning and troubleshooting. In production, give the
Modbus devices and servers fixed addresses (unique local `fd00::/8`, or global ones), that the firewall and
`allowed_hosts` filter.

### Listening socket

On Windows, the listening port is reserved with `SO_EXCLUSIVEADDRUSE`: no other socket (of the same process, of
another process or of another user account) can bind it while the server runs and take its connections (port
hijacking). The `SO_REUSEADDR` option, which allows this hijacking on Windows, is only set on the other systems,
where it allows a quick restart of the server on the same port. On macOS and the BSDs, it also lets a process of the
same user bind the same port on a more specific address (like `127.0.0.1` for a server on `0.0.0.0`) and get the
connections to this address: the processes of the other users can't.

On every system, another socket may bind the wildcard address (`0.0.0.0`) on the port of a server that listens on
a specific address (refused on Linux only): it only gets the connections to the other addresses of the host, never
those of the server.

On Windows, a server restarted on the same port may get an "address already in use" error for a short time, until
the connections of the previous one are fully closed by the system.

### Frame checks

- The MBAP header is checked (protocol ID must be 0, length from 2 to 254 bytes): an invalid header closes
  the connection, since the TCP stream can no longer be trusted.
- The PDU of a request is checked against its function: quantities in the ranges of the specification, a byte
  count that matches the quantity for the write functions, no missing data. An invalid request gets an
  "illegal data value" exception and the session stays in sync. Bytes after a complete and consistent request
  are ignored.
- An unexpected exception raised by user code (function handler, fallback, store wrapper, mapped range) is
  logged and answered with a "slave device failure" exception, the session is kept. Every value written by the
  request is restored (the request runs in a transaction of the data store). An exception raised by a change
  listener is logged only: the request is already committed. These errors are logged once, then once per 10 s at
  most (with the number of errors and the traceback of the last one), so a client that triggers the same bug in a
  loop can't fill the disk. The safety nets of the gateways (an unexpected error of the serial worker or of the
  Modbus/TCP client) are limited the same way.

## Client protections

- `timeout` is a limit for the whole request (send and receive), not for each socket operation: a server that
  sends its reply byte by byte can't hold the client longer than `timeout`.
- The reply is checked before use: MBAP header (transaction ID, protocol ID, length, unit ID), function code
  of the request, byte counts (exactly the data requested: a reply with more registers or bits, like the reply of
  another request, is an error), and the echo of the address and value/quantity for the write functions.
  An invalid MBAP header closes the connection.
- A request that the server may have read is never sent twice by the client: after a connection reset while waiting
  for the reply, the server may have executed it (and a read can have an effect too, like a counter cleared on read),
  the request fails and the application decides. Only a request whose send failed on a connection already reset (it
  never reached the server) is sent again once on a new connection (`auto_open` mode).
- The transaction ID is a 16 bits counter (from a random start value): two consecutive requests never share
  the same ID, so a late reply to a previous request is never taken for the current one.
- Parameters are checked before any network I/O (types, ranges, end of the address space). The host is an IPv4
  address in its standard form, an IPv6 address or a hostname: a legacy IPv4 form that the resolver reads as another
  address (`192.168.1`, `192.168.010.1`, `0x7f.1`, `2130706433`) is refused, so a typo never reaches another device
  and a check on the text of an address can't be bypassed with another notation.
- `mbtcp-client` never prints a control char sent by a server (the device identification texts are escaped, like
  `\x1b`), and in its `csv` output a device identification text that starts with `=`, `+`, `-` or `@` gets a
  leading `'`: a spreadsheet shows it as text, never runs it as a formula (CWE-1236).

## Known limitations

These points are not vulnerabilities of the code, but behaviors to take into account in the design of an
application.

- **`read_only` only knows the standard write function codes**: `read_only` filters the function code of a request,
  not what its handler does. A function added by the application (0x41 to 0x48 for example), or a standard function
  it replaced (like a 0x03 that also writes a counter), runs in read-only mode and can write the data store. Such a
  handler must test the mode at each call (it can change while the server runs):

  ```python
  @server.function(0x41)
  def set_register(req: Request) -> bytes:
      if server.read_only:
          raise IllegalFunction()
      ...
  ```

  Or wrap the store in a `ReadOnly` wrapper: it refuses every write of a client, whatever the function, as long as
  the handler writes through `req.transaction()`. A fallback handler is safe: in read-only mode, it only gets the
  read functions.
- **`ModbusClient` is not thread-safe**: share an instance between threads only with an external lock.
- **Client name resolution** is not limited by `timeout`, and the connection timeout applies to each address
  returned by the resolver in turn.
