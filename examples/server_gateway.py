#!/usr/bin/env python3

"""
Modbus/TCP basic gateway (RTU slave(s) attached)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

[pyModbusTCP server] -> [ModbusSerialWorker] -> [serial RTU devices]

Run this as root to listen on TCP privileged ports (<= 1024).

Open /dev/ttyUSB0 at 115200 bauds and relay it RTU messages to slave(s).
$ sudo ./server_gateway.py --baudrate 115200 /dev/ttyUSB0
"""

import argparse
import logging
import queue
import struct
from threading import Thread, Event
from queue import Queue
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.utils import crc16
from pyModbusTCP.constants import EXP_GATEWAY_PATH_UNAVAILABLE, EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
# need sudo pip install pyserial==3.4
from serial import Serial, serialutil


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
        # [address] + PDU
        tmp_raw = struct.pack('B', slave_ad) + raw_pdu
        # [address] + PDU + [CRC 16]
        tmp_raw += struct.pack('<H', crc16(tmp_raw))
        self.raw = tmp_raw


class ModbusSerialWorker(Thread):
    """ Main serial thread to manage I/O with RTU devices. """

    class _RtuQuery:
        """ Internal request container to deal with serial worker thread. """

        def __init__(self):
            self.completed = Event()
            self.request = ModbusRTUFrame()
            self.response = ModbusRTUFrame()

    def __init__(self, port, timeout=1.0, end_of_frame=0.05):
        super().__init__()
        # this thread is a daemon
        self.daemon = True
        # public
        self.serial_port = port
        self.timeout = timeout
        self.end_of_frame = end_of_frame
        # internal request queue
        # accept 5 simultaneous requests before overloaded exception is return
        self.rtu_queries_q = Queue(maxsize=5)

    def run(self):
        """Serial worker thread."""
        while True:
            # get next exchange from queue
            rtu_query = self.rtu_queries_q.get()
            # send to serial
            self.serial_port.reset_input_buffer()
            self.serial_port.write(rtu_query.request.raw)
            # receive from serial
            # wait for first byte of data until timeout delay
            self.serial_port.timeout = self.timeout
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
            rtu_query.response.raw = rx_raw
            # mark all as done
            rtu_query.completed.set()
            self.rtu_queries_q.task_done()

    def srv_engine_entry(self, session_data):
        """Server engine entry point (pass request to serial worker thread).

        :param session_data: server session data
        :type session_data: ModbusServer.SessionData
        """
        # init a serial exchange from session data
        rtu_query = ModbusSerialWorker._RtuQuery()
        rtu_query.request.build(raw_pdu=session_data.request.pdu.raw,
                                slave_ad=session_data.request.mbap.unit_id)
        try:
            # add a request in the serial worker queue, can raise queue.Full
            self.rtu_queries_q.put(rtu_query, block=False)
            # wait result
            rtu_query.completed.wait()
            # check receive frame status
            if rtu_query.response.is_valid:
                session_data.response.pdu.raw = rtu_query.response.pdu
                return
            # except status for slave failed to respond
            exp_status = EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
        except queue.Full:
            # except status for overloaded gateway
            exp_status = EXP_GATEWAY_PATH_UNAVAILABLE
        # return modbus exception
        func_code = rtu_query.request.function_code
        session_data.response.pdu.build_except(func_code=func_code, exp_status=exp_status)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='serial rate (default is 9600)')
    parser.add_argument('-t', '--timeout', type=float, default=1.0, help='timeout delay (default is 1.0 s)')
    parser.add_argument('-e', '--eof', type=float, default=0.05, help='end of frame delay (default is 0.05 s)')
    parser.add_argument('-d', '--debug', action='store_true', help='set debug mode')
    args = parser.parse_args()
    # init logging
    logging.basicConfig(level=logging.DEBUG if args.debug else None)
    logger = logging.getLogger(__name__)
    try:
        # init serial port
        logger.debug('Open serial port %s at %d bauds', args.device, args.baudrate)
        serial_port = Serial(port=args.device, baudrate=args.baudrate)
        # start serial worker thread
        logger.debug('Start serial worker thread')
        serial_worker = ModbusSerialWorker(serial_port, args.timeout, args.eof)
        serial_worker.start()
        # start modbus server with custom engine
        logger.debug('Start modbus server (%s, %d)', args.host, args.port)
        srv = ModbusServer(host=args.host, port=args.port, ext_engine=serial_worker.srv_engine_entry)
        srv.start()
    except serialutil.SerialException as e:
        logger.critical('Serial device error: %r', e)
        exit(1)
    except ModbusServer.Error as e:
        logger.critical('Modbus server error: %r', e)
        exit(2)
