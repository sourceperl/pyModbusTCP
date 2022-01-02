#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Modbus/TCP server
#
# run this as root to listen on TCP priviliged ports (<= 1024)
# default Modbus/TCP port is 502 so we prefix call with sudo
# add "--host 0.0.0.0" to listen on all available IPv4 addresses of the host
#
#   sudo ./server.py --host 0.0.0.0

import argparse
from pyModbusTCP.server import ModbusServer

if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    args = parser.parse_args()
    # start modbus server
    server = ModbusServer(host=args.host, port=args.port)
    server.start()
