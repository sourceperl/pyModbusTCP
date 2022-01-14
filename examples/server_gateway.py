#!/usr/bin/env python3

"""
Modbus/TCP gateway
~~~~~~~~~~~~~~~~~~

Run this as root to listen on TCP privileged ports (<= 1024).

Add "--host 0.0.0.0" to listen on all available IPv4 addresses of the host.
$ sudo ./server.py --host 0.0.0.0
"""

import argparse
import struct
from threading import Thread, Event
from queue import Queue
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.utils import crc16
from pyModbusTCP.constants import EXP_GATEWAY_PATH_UNAVAILABLE, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
# need sudo pip install pyserial==3.4
import serial


# some class
class ModbusRTUFrame:

    """ Modbus RTU frame container class. """

    def __init__(self, raw=b''):
        # public
        self.raw = raw

    @property
    def pdu(self):
        """Return PDU part of frame."""
        return self.raw[1:-2]

    @property
    def slave_address(self):
        """Return slave address part of frame."""
        return self.raw[0]

    @property
    def function_code(self):
        """Return function code part of frame."""
        return self.raw[1]

    @property
    def is_valid(self):
        """Check if frame is valid.

        :return: True if frame is valid
        :rtype: bool
        """
        return len(self.raw) > 4 and crc16(self.raw) == 0

    def build(self, raw_pdu, slave_ad):
        """Build a full modbus RTU message from PDU and slave address.

        :param raw_pdu: modbus as raw value
        :type raw_pdu: bytes
        :param slave_ad: address of the slave
        :type slave_ad: int
        """
        tmp_raw = struct.pack('B', slave_ad) + raw_pdu
        tmp_raw += struct.pack('<H', crc16(tmp_raw))
        self.raw = tmp_raw


class ModbusRTURequest:
    """ Modbus request container for deal with ModbusSerialWorker. """
    def __init__(self):
        self.completed = Event()
        self.tx_frame = ModbusRTUFrame()
        self.rx_frame = ModbusRTUFrame()


class ModbusSerialWorker(Thread):
    """ Main serial thread to manage I/O with RTU devices. """

    def __init__(self, serial_port):
        super().__init__()
        # this thread is a daemon
        self.daemon = True
        # public
        self.serial_port = serial_port
        self.requests_q = Queue(maxsize=100)

    def run(self):
        """ main requests processing loop. """
        while True:
            # get next request from queue
            request = self.requests_q.get()
            # send over serial
            self.serial_port.reset_input_buffer()
            self.serial_port.write(request.tx_frame.raw)
            # receive loop
            rx_raw = b''
            while True:
                rx_chunk = self.serial_port.read(256)
                if not rx_chunk:
                    break
                rx_raw += rx_chunk
            request.rx_frame.raw = rx_raw
            # mark all as done
            request.completed.set()
            self.requests_q.task_done()


# some function
def gw_engine(in_mbap, in_pdu):
    """ deal with serial worker to process server input PDU """
    # init a request with input PDU
    request = ModbusRTURequest()
    request.tx_frame.build(raw_pdu=in_pdu.raw, slave_ad=in_mbap.unit_id)
    # add a request in the serial worker queue
    serial_worker.requests_q.put(request, block=False)
    # print(f'schedule request {request.tx_frame.raw}')
    # wait result available
    request.completed.wait()
    # print(f'receive: {request.rx_frame.raw} is valid: {request.rx_frame.is_valid}')
    if request.rx_frame.is_valid:
        return ModbusServer.PDU(request.rx_frame.pdu)
    else:
        return ModbusServer.PDU().build_except(func_code=request.tx_frame.function_code,
                                               exp_status=EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('device', type=str, help='Serial device (like /dev/ttyUSB0)')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='Host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600)
    parser.add_argument('-t', '--timeout', type=float, help='override default timeout (0.050s)')
    args = parser.parse_args()
    # init serial port
    serial_timeout = args.timeout if args.timeout else 0.050
    serial_port = serial.Serial(port=args.device, baudrate=args.baudrate, timeout=serial_timeout)
    # start main thread
    serial_worker = ModbusSerialWorker(serial_port)
    serial_worker.start()
    # init and launch modbus server with custom engine
    srv = ModbusServer(host=args.host, port=args.port, ext_engine=gw_engine)
    srv.start()
