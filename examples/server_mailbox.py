#!/usr/bin/env python3

"""
Modbus/TCP server with a command mailbox
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Holding registers of the mailbox:

    @0  first operand   (written by the client)
    @1  second operand  (written by the client)
    @2  command         (written by the client: 1 add, 2 multiply)
    @3  result          (written by the server)

The command register is a mapped range: writing it runs the command inside
the client request. With function 0x17 (write then read), the client sends the
operands and the command, and gets the result in the same request.

The server listens on localhost, on a free port chosen by the system (or the
one of --port), until Ctrl-C. With --demo, the script runs a client against
it, then stops:
$ ./server_mailbox.py --demo
$ ./server_mailbox.py --port 5020        # then: mbtcp-client -p 5020 write-read 0 20 22 1 -n 4
"""

import argparse
import logging

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.server import (
    IllegalDataValue,
    MemoryStore,
    ModbusServer,
    RequestContext,
    Space,
)

# addresses of the mailbox registers
OP1, OP2, CMD, RESULT = 0, 1, 2, 3
OPERATIONS = {1: lambda a, b: a + b, 2: lambda a, b: a * b}

store = MemoryStore()


def read_command_reg(address: int, count: int, ctx: RequestContext | None) -> list[int]:
    """Called when a client reads the command register: it always reads 0."""
    return [0] * count


def write_command_reg(address: int, values: list[int], ctx: RequestContext | None) -> None:
    """Called when a client writes the command register: run the command."""
    command = values[0]
    if command not in OPERATIONS:
        # the whole request is cancelled, the client gets an "illegal data value" exception
        raise IllegalDataValue()
    a, b = store.app.holding_registers.read(OP1, 2)
    store.app.holding_registers.write(RESULT, [OPERATIONS[command](a, b) & 0xFFFF])


# map the command register
store.map(Space.HOLDING_REGISTERS, CMD, 1, read=read_command_reg, write=write_command_reg)


def demo(host: str, port: int) -> None:
    """Send some commands, print their result."""
    with ModbusClient(host=host, port=port) as client:
        for a, b, command in [(20, 22, 1), (3, 7, 2), (1, 1, 9)]:
            # write the operands then the command, read the whole mailbox back: one request
            mailbox = client.write_read_multiple_registers(OP1, [a, b, command], OP1, 4)
            if mailbox is not None:
                print(f'{a}, {b}, command {command} -> result {mailbox[RESULT]}')
            else:
                print(f'{a}, {b}, command {command} -> {client.last_except_as_txt}')


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=0, help='TCP port (default: 0, a free port)')
    parser.add_argument('-d', '--debug', action='store_true', help='set debug mode')
    parser.add_argument('--demo', action='store_true', help='run a demo client against the server, then stop')
    args = parser.parse_args()

    # init logging
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)

    server = ModbusServer(host=args.host, port=args.port, store=store, no_block=True)
    try:
        host, port = server.start()
        print(f'server on {host}:{port}')
        if args.demo:
            demo(host, port)
        else:
            server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
