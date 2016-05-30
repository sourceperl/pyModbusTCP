#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Modbus/TCP server

import argparse
from pyModbusTCP.server import ModbusServer

if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port')
    args = parser.parse_args()
    # start modbus server
    server = ModbusServer(host=args.host, port=args.port)
    server.start()
