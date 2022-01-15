#!/usr/bin/env python3

"""
Modbus/TCP basic gateway
~~~~~~~~~~~~~~~~~~~~~~~~

[pyModbusTCP server] -> [ModbusSerialWorker] -> [serial RTU devices]

Run this as root to listen on TCP privileged ports (<= 1024).

Open /dev/ttyUSB0 at 115200 bauds and relay it RTU messages to slave(s)
$ sudo ./server_gateway.py --baudrate 115200 /dev/ttyUSB0
"""

import argparse
import queue
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
        # [address] + PDU
        tmp_raw = struct.pack('B', slave_ad) + raw_pdu
        # [address] + PDU + [CRC 16]
        tmp_raw += struct.pack('<H', crc16(tmp_raw))
        self.raw = tmp_raw


class ModbusSerialWorker(Thread):
    """ Main serial thread to manage I/O with RTU devices. """

    class _SerialRequest:
        """ Internal request container to deal with serial worker thread. """

        def __init__(self):
            self.completed = Event()
            self.tx_frame = ModbusRTUFrame()
            self.rx_frame = ModbusRTUFrame()

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
        self.requests_q = Queue(maxsize=5)

    def run(self):
        """Serial worker thread."""
        while True:
            # get next request from queue
            request = self.requests_q.get()
            # send to serial
            self.serial_port.reset_input_buffer()
            self.serial_port.write(request.tx_frame.raw)
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
            request.rx_frame.raw = rx_raw
            # mark all as done
            request.completed.set()
            self.requests_q.task_done()

    def srv_engine_entry(self, mbap, pdu):
        """Server engine entry point (pass request to serial worker thread)."""
        # init a request with input PDU
        request = ModbusSerialWorker._SerialRequest()
        request.tx_frame.build(raw_pdu=pdu.raw, slave_ad=mbap.unit_id)
        try:
            # add a request in the serial worker queue, can raise queue.Full
            self.requests_q.put(request, block=False)
            # wait result
            request.completed.wait()
            # check receive frame status
            if request.rx_frame.is_valid:
                return ModbusServer.PDU(request.rx_frame.pdu)
            # except status for slave failed to respond
            exp_status = EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
        except queue.Full:
            # except status for overloaded gateway
            exp_status = EXP_GATEWAY_PATH_UNAVAILABLE
        # return modbus exception
        func_code = request.tx_frame.function_code
        return ModbusServer.PDU().build_except(func_code=func_code, exp_status=exp_status)


if __name__ == '__main__':
    # parse args
    parser = argparse.ArgumentParser()
    parser.add_argument('device', type=str, help='serial device (like /dev/ttyUSB0)')
    parser.add_argument('-H', '--host', type=str, default='localhost', help='host (default: localhost)')
    parser.add_argument('-p', '--port', type=int, default=502, help='TCP port (default: 502)')
    parser.add_argument('-b', '--baudrate', type=int, default=9600, help='serial rate (default is 9600)')
    parser.add_argument('-t', '--timeout', type=float, default=1.0, help='timeout delay (default is 1.0 s)')
    parser.add_argument('-e', '--eof', type=float, default=0.05, help='end of frame delay (default is 0.05 s)')
    args = parser.parse_args()
    # init serial port
    serial_port = serial.Serial(port=args.device, baudrate=args.baudrate)
    # start main thread
    serial_worker = ModbusSerialWorker(serial_port, args.timeout, args.eof)
    serial_worker.start()
    # init and launch modbus server with custom engine
    srv = ModbusServer(host=args.host, port=args.port, ext_engine=serial_worker.srv_engine_entry)
    srv.start()
