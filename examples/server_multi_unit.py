#!/usr/bin/env python3

"""
Modbus/TCP server that simulates two devices
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One data store per unit id, like a gateway with two devices behind it:

    unit 1: a power meter, read-only, values as 32 bits floats (2 registers)
        input registers @0 voltage (V), @2 current (A), @4 power (W)
    unit 2: a valve controller
        coil @0 open command (written by a client)
        discrete input @0 valve opened (position feedback)

A request for another unit id gets a "gateway path unavailable" exception.
The application writes the measurements with the typed access of the store
(write_float32), in one transaction: a client never reads a voltage and a
power of two different updates.

Port 502 (the default Modbus/TCP port) is a privileged port on Unix systems.
Never run this script as root (or with sudo) to listen on it: give the
CAP_NET_BIND_SERVICE capability to the interpreter of a dedicated virtual
environment, or use a port above 1023 (-p 5020).
See "Listen on the standard Modbus port 502 without root" in examples/README.md.

The server listens on localhost by default. Add "--host" with the address of a
network interface to accept remote clients (or 0.0.0.0 for all IPv4 interfaces,
only if every network of the host is trusted).
$ ./server_multi_unit.py --port 5020
Then read the voltage with mbtcp-client:
$ mbtcp-client -p 5020 -u 1 read-input-regs 0 -n 6
"""

import argparse
import math
import time

from pyModbusTCP.server import ChangeEvent, MemoryStore, ModbusServer, ReadOnly

# unit 1: the power meter, read-only for the clients, 6 input registers
meter = ReadOnly(MemoryStore(0, input_registers=6))
# unit 2: the valve, 1 coil and 1 discrete input
valve = MemoryStore(0, coils=1, discrete_inputs=1)


def on_valve_command(event: ChangeEvent) -> None:
    """The open command changed: move the valve (its position feedback follows the command)."""
    for change in event.changes:
        valve.app.discrete_inputs.write(0, [change.new])
        print('valve', 'opened' if change.new else 'closed', 'by', event.ctx.client.address if event.ctx else '?')


def update_measurements(t: float) -> None:
    voltage = 230.0 + 2.0 * math.sin(t / 10)
    current = 4.0 + math.sin(t / 3)
    regs = meter.app.input_registers
    # the 3 values of an update are seen together by the clients
    with meter.app.transaction():
        regs.write_float32(0, voltage)
        regs.write_float32(2, current)
        regs.write_float32(4, voltage * current)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()

    valve.subscribe(on_valve_command)

    # one store per unit id
    server = ModbusServer(host=args.host, port=args.port, store={1: meter, 2: valve}, no_block=True)
    server.start()
    try:
        while server.is_running:
            update_measurements(time.time())
            server.wait(timeout=1.0)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
