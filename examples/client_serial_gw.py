#!/usr/bin/env python3

"""
Modbus RTU to TCP basic gateway (master attached)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Modbus master device -> [Modbus RTU] -> client_serial_gw -> [Modbus/TCP] -> Modbus server

Open /dev/ttyUSB0 at 115200 bauds and relay RTU messages to modbus server for slave address 30.
$ ./client_serial_gw.py /dev/ttyUSB0 --baudrate 115200 --address 30
"""

import argparse
import logging
import struct

# need sudo pip3 install pyserial==3.4
from serial import Serial, serialutil

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.constants import EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
from pyModbusTCP.utils import crc16


# some class
class ModbusRTUFrame:
    """ Modbus RTU frame container class. """

    def __init__(self, raw=b''):
        # public
        self.raw = raw

    def __repr__(self) -> str:
        return self.as_hex

    @property
    def as_hex(self) -> str:
        """Return RAW frame as a hex string."""
        return '-'.join(['%02X' % x for x in self.raw])

    @property
    def pdu(self):
        """Return PDU part of frame."""
        return self.raw[1:-2]

    @property
    def slave_addr(self):
        """Return slave address part of frame."""
        return self.raw[0]

    @property
    def function_code(self):
        """Return function code part of frame."""
        return self.raw[1]

    @property
    def is_set(self):
        """Check if frame is set

        :return: True if frame is set
        :rtype: bool
        """
        return bool(self.raw)

    @property
    def is_valid(self):
        """Check if frame is valid.

        :return: True if frame is valid
        :rtype: bool
        """
        return len(self.raw) > 4 and crc16(self.raw) == 0

    def build(self, raw_pdu, slave_addr):
        """Build a full modbus RTU message from PDU and slave address.

        :param raw_pdu: modbus as raw value
        :type raw_pdu: bytes
        :param slave_addr: address of the slave
        :type slave_addr: int
        """
        # [address] + PDU
        tmp_raw = struct.pack('B', slave_addr) + raw_pdu
        # [address] + PDU + [CRC 16]
        tmp_raw += struct.pack('<H', crc16(tmp_raw))
        self.raw = tmp_raw


class SlaveSerialWorker:
    """ A serial worker to manage I/O with RTU master device. """

    def __init__(self, port, end_of_frame=0.05):
        # public
        self.serial_port = port
        self.end_of_frame = end_of_frame
        self.request = ModbusRTUFrame()
        self.response = ModbusRTUFrame()

    def handle_request(self):
        """Default PDU request processing here, you must implement it in your app."""
        raise RuntimeError('implement this')

    def run(self):
        """Serial worker process."""
        # flush serial buffer
        self.serial_port.reset_input_buffer()
        # request loop
        while True:
            # init a new transaction
            self.request = ModbusRTUFrame()
            self.response = ModbusRTUFrame()
            # receive from serial
            # wait for first byte of data
            self.serial_port.timeout = None
            rx_raw = self.serial_port.read(1)
            # if ok, wait for the remaining
            if rx_raw:
                self.serial_port.timeout = self.end_of_frame
                # wait for next bytes of data until end of frame delay
                while True:
                    rx_chunk = self.serial_port.read(256)
                    if not rx_chunk:
                        break
                    else:
                        rx_raw += rx_chunk
            # store the receipt frame
            self.request.raw = rx_raw
            crc_ok = self.request.is_valid
            # log of received items for debugging purposes
            logger.debug('Receive: %s (CRC %s)' % (self.request, "OK" if crc_ok else "ERROR"))
            # just ignore current frame on CRC error
            if not crc_ok:
                continue
            # relay PDU of request to modbus server
            self.handle_request()
            # if a response frame is set sent it
            if self.response.is_set:
                # log sent items for debugging purposes
                logger.debug('Send: %s' % self.response)
                self.serial_port.write(self.response.raw)


class Serial2ModbusClient:
    """ Customize a slave serial worker for map a modbus TCP client. """

    def __init__(self, serial_w, mbus_cli, slave_addr=1, allow_bcast=False):
        """Serial2ModbusClient constructor.

        :param serial_w: a SlaveSerialWorker instance
        :type serial_w: SlaveSerialWorker
        :param mbus_cli: a ModbusClient instance
        :type mbus_cli: ModbusClient
        :param slave_addr: modbus slave address
        :type slave_addr: int
        :param allow_bcast: allow processing broadcast frames (slave @0)
        :type allow_bcast: bool
        """
        # public
        self.serial_w = serial_w
        self.mbus_cli = mbus_cli
        self.slave_addr = slave_addr
        self.allow_bcast = allow_bcast
        # replace serial worker default request handler by Serial2ModbusClient one
        self.serial_w.handle_request = self._handle_request

    def _handle_request(self):
        """Request handler for SlaveSerialWorker"""
        # broadcast/unicast frame ?
        if self.serial_w.request.slave_addr == 0 and self.allow_bcast:
            # if config allow it, process a broadcast request (=> process it, but don't respond)
            self.mbus_cli.custom_request(self.serial_w.request.pdu)
        elif self.serial_w.request.slave_addr == self.slave_addr:
            # process unicast request
            resp_pdu = self.mbus_cli.custom_request(self.serial_w.request.pdu)
            # if no error, format a response frame
            if resp_pdu:
                # regular response from Modbus/TCP client
                self.serial_w.response.build(raw_pdu=resp_pdu, slave_addr=self.serial_w.request.slave_addr)
            else:
                # exception response
                exp_pdu = struct.pack('BB', self.serial_w.request.function_code + 0x80,
                                      EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND)
                self.serial_w.response.build(raw_pdu=exp_pdu, slave_addr=self.serial_w.request.slave_addr)

    def run(self):
        """Start serial processing."""
        self.serial_w.run()


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('serial_device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-d', '--debug', action='store_true', help='debug mode')
    parser.add_argument('-a', '--address', type=int, default=1, help='slave address (default is 1)')
    parser.add_argument('--allow-broadcast', action='store_true', help='serial allow broadcast frame (to address 0)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='serial rate (default is 9600)')
    parser.add_argument('-e', '--eof', type=float, default=0.05, help='serial end of frame delay in s (default: 0.05)')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='server host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='server TCP port (default: 502)')
    parser.add_argument('-t', '--timeout', type=float, default=1.0, help='server timeout delay in s (default: 1.0)')
    args = parser.parse_args()
    # init logging
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    logger = logging.getLogger(__name__)
    try:
        # init serial port
        logger.info('Open serial port %s at %d bauds (eof = %.2fs)', args.serial_device, args.baudrate, args.eof)
        serial_port = Serial(port=args.serial_device, baudrate=args.baudrate)
        # start modbus client as request relay
        logger.info('Connect to modbus server at %s:%d (timeout = %.2fs)', args.host, args.port, args.timeout)
        mbus_cli = ModbusClient(host=args.host, port=args.port, unit_id=1, timeout=args.timeout)
        # init serial worker
        serial_worker = SlaveSerialWorker(serial_port, end_of_frame=args.eof)
        # start Serial2ModbusClient
        logger.info('Start serial worker (slave address = %d)' % args.address)
        Serial2ModbusClient(mbus_cli=mbus_cli, serial_w=serial_worker,
                            slave_addr=args.address, allow_bcast=args.allow_broadcast).run()
    except serialutil.SerialException as e:
        logger.critical('Serial device error: %r', e)
        exit(1)
