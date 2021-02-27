# -*- coding: utf-8 -*-

# Python module: ModbusServer class (ModBus/TCP Server)

from . import constants as const
from .utils import test_bit, set_bit
import socket
import struct
from threading import Lock, Thread

# for python2 compatibility
try:
    from socketserver import BaseRequestHandler, ThreadingTCPServer
except ImportError:
    from SocketServer import BaseRequestHandler, ThreadingTCPServer


class DataBank:

    """ Data class for thread safe access to bits and words space """

    bits_lock = Lock()
    bits = [False] * 0x10000
    words_lock = Lock()
    words = [0] * 0x10000

    @classmethod
    def get_bits(cls, address, number=1):
        """Read data on server bits space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :returns: list of bool or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with cls.bits_lock:
            if (address >= 0) and (address + number <= len(cls.bits)):
                return cls.bits[address: number + address]
            else:
                return None

    @classmethod
    def set_bits(cls, address, bit_list):
        """Write data to server bits space

        :param address: start address
        :type address: int
        :param bit_list: a list of bool to write
        :type bit_list: list
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if bit_list members cannot be convert to bool
        """
        # ensure bit_list values are bool
        bit_list = [bool(b) for b in bit_list]
        # secure copy of data to list used by server thread
        with cls.bits_lock:
            if (address >= 0) and (address + len(bit_list) <= len(cls.bits)):
                cls.bits[address: address + len(bit_list)] = bit_list
                return True
            else:
                return None

    @classmethod
    def get_words(cls, address, number=1):
        """Read data on server words space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :returns: list of int or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with cls.words_lock:
            if (address >= 0) and (address + number <= len(cls.words)):
                return cls.words[address: number + address]
            else:
                return None

    @classmethod
    def set_words(cls, address, word_list):
        """Write data to server words space

        :param address: start address
        :type address: int
        :param word_list: a list of word to write
        :type word_list: list
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if word_list members cannot be convert to int
        """
        # ensure word_list values are int with a max bit length of 16
        word_list = [int(w) & 0xffff for w in word_list]
        # secure copy of data to list used by server thread
        with cls.words_lock:
            if (address >= 0) and (address + len(word_list) <= len(cls.words)):
                cls.words[address: address + len(word_list)] = word_list
                return True
            else:
                return None


class ModbusServer(object):

    """Modbus TCP server"""

    class ModbusService(BaseRequestHandler):

        def recv_all(self, size):
            if hasattr(socket, "MSG_WAITALL"):
                data = self.request.recv(size, socket.MSG_WAITALL)
            else:
                # Windows lacks MSG_WAITALL
                data = b''
                while len(data) < size:
                    data += self.request.recv(size - len(data))
            return data

        def handle(self):
            while True:
                rx_head = self.recv_all(7)
                # close connection if no standard 7 bytes header
                if not (rx_head and len(rx_head) == 7):
                    break
                # decode header
                (rx_hd_tr_id, rx_hd_pr_id,
                 rx_hd_length, rx_hd_unit_id) = struct.unpack('>HHHB', rx_head)
                # close connection if frame header content inconsistency
                if not ((rx_hd_pr_id == 0) and (2 < rx_hd_length < 256)):
                    break
                # receive body
                rx_body = self.recv_all(rx_hd_length - 1)
                # close connection if lack of bytes in frame body
                if not (rx_body and (len(rx_body) == rx_hd_length - 1)):
                    break
                # body decode: function code
                rx_bd_fc = struct.unpack('B', rx_body[0:1])[0]
                # close connection if function code is inconsistent
                if rx_bd_fc > 0x7F:
                    break
                # default except status
                exp_status = const.EXP_NONE
                # functions Read Coils (0x01) or Read Discrete Inputs (0x02)
                if rx_bd_fc in (const.READ_COILS, const.READ_DISCRETE_INPUTS):
                    (b_address, b_count) = struct.unpack('>HH', rx_body[1:])
                    # check quantity of requested bits
                    if 0x0001 <= b_count <= 0x07D0:
                        bits_l = DataBank.get_bits(b_address, b_count)
                        if bits_l:
                            # allocate bytes list
                            b_size = int(b_count / 8)
                            b_size += 1 if (b_count % 8) else 0
                            bytes_l = [0] * b_size
                            # populate bytes list with data bank bits
                            for i, item in enumerate(bits_l):
                                if item:
                                    byte_i = int(i/8)
                                    bytes_l[byte_i] = set_bit(bytes_l[byte_i], i % 8)
                            # format body of frame with bits
                            tx_body = struct.pack('BB', rx_bd_fc, len(bytes_l))
                            # add bytes with bits
                            for byte in bytes_l:
                                tx_body += struct.pack('B', byte)
                        else:
                            exp_status = const.EXP_DATA_ADDRESS
                    else:
                        exp_status = const.EXP_DATA_VALUE
                # functions Read Holding Registers (0x03) or Read Input Registers (0x04)
                elif rx_bd_fc in (const.READ_HOLDING_REGISTERS, const.READ_INPUT_REGISTERS):
                    (w_address, w_count) = struct.unpack('>HH', rx_body[1:])
                    # check quantity of requested words
                    if 0x0001 <= w_count <= 0x007D:
                        words_l = DataBank.get_words(w_address, w_count)
                        if words_l:
                            # format body of frame with words
                            tx_body = struct.pack('BB', rx_bd_fc, w_count * 2)
                            for word in words_l:
                                tx_body += struct.pack('>H', word)
                        else:
                            exp_status = const.EXP_DATA_ADDRESS
                    else:
                        exp_status = const.EXP_DATA_VALUE
                # function Write Single Coil (0x05)
                elif rx_bd_fc is const.WRITE_SINGLE_COIL:
                    (b_address, b_value) = struct.unpack('>HH', rx_body[1:])
                    f_b_value = bool(b_value == 0xFF00)
                    if DataBank.set_bits(b_address, [f_b_value]):
                        # send write ok frame
                        tx_body = struct.pack('>BHH', rx_bd_fc, b_address, b_value)
                    else:
                        exp_status = const.EXP_DATA_ADDRESS
                # function Write Single Register (0x06)
                elif rx_bd_fc is const.WRITE_SINGLE_REGISTER:
                    (w_address, w_value) = struct.unpack('>HH', rx_body[1:])
                    if DataBank.set_words(w_address, [w_value]):
                        # send write ok frame
                        tx_body = struct.pack('>BHH', rx_bd_fc, w_address, w_value)
                    else:
                        exp_status = const.EXP_DATA_ADDRESS
                # function Write Multiple Coils (0x0F)
                elif rx_bd_fc is const.WRITE_MULTIPLE_COILS:
                    (b_address, b_count, byte_count) = struct.unpack('>HHB', rx_body[1:6])
                    # check quantity of updated coils
                    if (0x0001 <= b_count <= 0x07B0) and (byte_count >= (b_count/8)):
                        # allocate bits list
                        bits_l = [False] * b_count
                        # populate bits list with bits from rx frame
                        for i, item in enumerate(bits_l):
                            b_bit_pos = int(i/8)+6
                            b_bit_val = struct.unpack('B', rx_body[b_bit_pos:b_bit_pos+1])[0]
                            bits_l[i] = test_bit(b_bit_val, i % 8)
                        # write words to data bank
                        if DataBank.set_bits(b_address, bits_l):
                            # send write ok frame
                            tx_body = struct.pack('>BHH', rx_bd_fc, b_address, b_count)
                        else:
                            exp_status = const.EXP_DATA_ADDRESS
                    else:
                        exp_status = const.EXP_DATA_VALUE
                # function Write Multiple Registers (0x10)
                elif rx_bd_fc is const.WRITE_MULTIPLE_REGISTERS:
                    (w_address, w_count, byte_count) = struct.unpack('>HHB', rx_body[1:6])
                    # check quantity of updated words
                    if (0x0001 <= w_count <= 0x007B) and (byte_count == w_count * 2):
                        # allocate words list
                        words_l = [0] * w_count
                        # populate words list with words from rx frame
                        for i, item in enumerate(words_l):
                            w_offset = i * 2 + 6
                            words_l[i] = struct.unpack('>H', rx_body[w_offset:w_offset + 2])[0]
                        # write words to data bank
                        if DataBank.set_words(w_address, words_l):
                            # send write ok frame
                            tx_body = struct.pack('>BHH', rx_bd_fc, w_address, w_count)
                        else:
                            exp_status = const.EXP_DATA_ADDRESS
                    else:
                        exp_status = const.EXP_DATA_VALUE
                else:
                    exp_status = const.EXP_ILLEGAL_FUNCTION
                # check exception
                if exp_status != const.EXP_NONE:
                    # format body of frame with exception status
                    tx_body = struct.pack('BB', rx_bd_fc + 0x80, exp_status)
                # build frame header
                tx_head = struct.pack('>HHHB', rx_hd_tr_id, rx_hd_pr_id, len(tx_body) + 1, rx_hd_unit_id)
                # send frame
                self.request.send(tx_head + tx_body)
            self.request.close()

    def __init__(self, host='localhost', port=const.MODBUS_PORT, no_block=False, ipv6=False):
        """Constructor

        Modbus server constructor.

        :param host: hostname or IPv4/IPv6 address server address (optional)
        :type host: str
        :param port: TCP port number (optional)
        :type port: int
        :param no_block: set no block mode, in this mode start() return (optional)
        :type no_block: bool
        :param ipv6: use ipv6 stack
        :type ipv6: bool
        """
        # public
        self.host = host
        self.port = port
        self.no_block = no_block
        self.ipv6 = ipv6
        # private
        self._running = False
        self._service = None
        self._serve_th = None

    def start(self):
        """Start the server.

        Do nothing if server is already running.
        This function will block if no_block is not set to True.
        """
        if not self.is_run:
            # set class attribute
            ThreadingTCPServer.address_family = socket.AF_INET6 if self.ipv6 else socket.AF_INET
            ThreadingTCPServer.daemon_threads = True
            # init server
            self._service = ThreadingTCPServer((self.host, self.port), self.ModbusService, bind_and_activate=False)
            # set socket options
            self._service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # TODO test no_delay with bench
            self._service.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # bind and activate
            self._service.server_bind()
            self._service.server_activate()
            # serve request
            if self.no_block:
                self._serve_th = Thread(target=self._serve)
                self._serve_th.daemon = True
                self._serve_th.start()
            else:
                self._serve()

    def stop(self):
        """Stop the server.

        Do nothing if server is already not running.
        """
        if self.is_run:
            self._service.shutdown()
            self._service.server_close()

    @property
    def is_run(self):
        """Return True if server running.

        """
        return self._running

    def _serve(self):
        try:
            self._running = True
            self._service.serve_forever()
        except:
            self._service.server_close()
            raise
        finally:
            self._running = False
