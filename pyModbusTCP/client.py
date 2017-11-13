# -*- coding: utf-8 -*-

# Python module: ModbusClient class (Client ModBus/TCP)

from . import constants as const
from .utils import crc16, set_bit
import re
import socket
import select
import struct
import random


class ModbusClient:

    """Modbus TCP client"""

    def __init__(self, host=None, port=None, unit_id=None, timeout=None,
                 debug=None, auto_open=None, auto_close=None):
        """Constructor

        Modbus server params (host, port) can be set here or with host(), port()
        functions. Same for debug option.

        Use functions avoid to launch ValueError except if params is incorrect.

        :param host: hostname or IPv4/IPv6 address server address (optional)
        :type host: str
        :param port: TCP port number (optional)
        :type port: int
        :param unit_id: unit ID (optional)
        :type unit_id: int
        :param timeout: socket timeout in seconds (optional)
        :type timeout: float
        :param debug: debug state (optional)
        :type debug: bool
        :param auto_open: auto TCP connect (optional)
        :type auto_open: bool
        :param auto_close: auto TCP close (optional)
        :type auto_close: bool
        :return: Object ModbusClient
        :rtype: ModbusClient
        :raises ValueError: if a set parameter value is incorrect
        """
        # object vars
        self.__hostname = 'localhost'
        self.__port = const.MODBUS_PORT
        self.__unit_id = 1
        self.__timeout = 30.0                # socket timeout
        self.__debug = False                 # debug trace on/off
        self.__auto_open = False             # auto TCP connect
        self.__auto_close = False            # auto TCP close
        self.__mode = const.MODBUS_TCP       # default is Modbus/TCP
        self.__sock = None                   # socket handle
        self.__hd_tr_id = 0                  # store transaction ID
        self.__version = const.VERSION       # version number
        self.__last_error = const.MB_NO_ERR  # last error code
        self.__last_except = 0               # last expect code
        # set host
        if host:
            if not self.host(host):
                raise ValueError('host value error')
        # set port
        if port:
            if not self.port(port):
                raise ValueError('port value error')
        # set unit_id
        if unit_id is not None:
            if self.unit_id(unit_id) is None:
                raise ValueError('unit_id value error')
        # set timeout
        if timeout:
            if not self.timeout(timeout):
                raise ValueError('timeout value error')
        # set debug
        if debug:
            if not self.debug(debug):
                raise ValueError('debug value error')
        # set auto_open
        if auto_open:
            if not self.auto_open(auto_open):
                raise ValueError('auto_open value error')
        # set auto_close
        if auto_close:
            if not self.auto_close(auto_close):
                raise ValueError('auto_close value error')

    def version(self):
        """Get package version

        :return: current version of the package (like "0.0.1")
        :rtype: str
        """
        return self.__version

    def last_error(self):
        """Get last error code

        :return: last error code
        :rtype: int
        """
        return self.__last_error

    def last_except(self):
        """Get last except code

        :return: last except code
        :rtype: int
        """
        return self.__last_except

    def host(self, hostname=None):
        """Get or set host (IPv4/IPv6 or hostname like 'plc.domain.net')

        :param hostname: hostname or IPv4/IPv6 address or None for get value
        :type hostname: str or None
        :returns: hostname or None if set fail
        :rtype: str or None
        """
        if (hostname is None) or (hostname == self.__hostname):
            return self.__hostname
        # when hostname change ensure old socket is close
        self.close()
        # IPv4 ?
        try:
            socket.inet_pton(socket.AF_INET, hostname)
            self.__hostname = hostname
            return self.__hostname
        except socket.error:
            pass
        # IPv6 ?
        try:
            socket.inet_pton(socket.AF_INET6, hostname)
            self.__hostname = hostname
            return self.__hostname
        except socket.error:
            pass
        # DNS name ?
        if re.match('^[a-z][a-z0-9\.\-]+$', hostname):
            self.__hostname = hostname
            return self.__hostname
        else:
            return None

    def port(self, port=None):
        """Get or set TCP port

        :param port: TCP port number or None for get value
        :type port: int or None
        :returns: TCP port or None if set fail
        :rtype: int or None
        """
        if (port is None) or (port == self.__port):
            return self.__port
        # when port change ensure old socket is close
        self.close()
        # valid port ?
        if 0 < int(port) < 65536:
            self.__port = int(port)
            return self.__port
        else:
            return None

    def unit_id(self, unit_id=None):
        """Get or set unit ID field

        :param unit_id: unit ID (0 to 255) or None for get value
        :type unit_id: int or None
        :returns: unit ID or None if set fail
        :rtype: int or None
        """
        if unit_id is None:
            return self.__unit_id
        if 0 <= int(unit_id) < 256:
            self.__unit_id = int(unit_id)
            return self.__unit_id
        else:
            return None

    def timeout(self, timeout=None):
        """Get or set timeout field

        :param timeout: socket timeout in seconds or None for get value
        :type timeout: float or None
        :returns: timeout or None if set fail
        :rtype: float or None
        """
        if timeout is None:
            return self.__timeout
        if 0 < float(timeout) < 3600:
            self.__timeout = float(timeout)
            return self.__timeout
        else:
            return None

    def debug(self, state=None):
        """Get or set debug mode

        :param state: debug state or None for get value
        :type state: bool or None
        :returns: debug state or None if set fail
        :rtype: bool or None
        """
        if state is None:
            return self.__debug
        self.__debug = bool(state)
        return self.__debug

    def auto_open(self, state=None):
        """Get or set automatic TCP connect mode

        :param state: auto_open state or None for get value
        :type state: bool or None
        :returns: auto_open state or None if set fail
        :rtype: bool or None
        """
        if state is None:
            return self.__auto_open
        self.__auto_open = bool(state)
        return self.__auto_open

    def auto_close(self, state=None):
        """Get or set automatic TCP close mode (after each request)

        :param state: auto_close state or None for get value
        :type state: bool or None
        :returns: auto_close state or None if set fail
        :rtype: bool or None
        """
        if state is None:
            return self.__auto_close
        self.__auto_close = bool(state)
        return self.__auto_close

    def mode(self, mode=None):
        """Get or set modbus mode (TCP or RTU)

        :param mode: mode (MODBUS_TCP/MODBUS_RTU) to set or None for get value
        :type mode: int
        :returns: mode or None if set fail
        :rtype: int or None
        """
        if mode is None:
            return self.__mode
        if mode == const.MODBUS_TCP or mode == const.MODBUS_RTU:
            self.__mode = mode
            return self.__mode
        else:
            return None

    def open(self):
        """Connect to modbus server (open TCP connection)

        :returns: connect status (True if open)
        :rtype: bool
        """
        # restart TCP if already open
        if self.is_open():
            self.close()
        # init socket and connect
        # list available sockets on the target host/port
        # AF_xxx : AF_INET -> IPv4, AF_INET6 -> IPv6,
        #          AF_UNSPEC -> IPv6 (priority on some system) or 4
        # list available socket on target host
        for res in socket.getaddrinfo(self.__hostname, self.__port,
                                      socket.AF_UNSPEC, socket.SOCK_STREAM):
            af, sock_type, proto, canon_name, sa = res
            try:
                self.__sock = socket.socket(af, sock_type, proto)
            except socket.error:
                self.__sock = None
                continue
            try:
                self.__sock.settimeout(self.__timeout)
                self.__sock.connect(sa)
            except socket.error:
                self.__sock.close()
                self.__sock = None
                continue
            break
        # check connect status
        if self.__sock is not None:
            return True
        else:
            self.__last_error = const.MB_CONNECT_ERR
            self.__debug_msg('connect error')
            return False

    def is_open(self):
        """Get status of TCP connection

        :returns: status (True for open)
        :rtype: bool
        """
        return self.__sock is not None

    def close(self):
        """Close TCP connection

        :returns: close status (True for close/None if already close)
        :rtype: bool or None
        """
        if self.__sock:
            self.__sock.close()
            self.__sock = None
            return True
        else:
            return None

    def read_coils(self, bit_addr, bit_nb=1):
        """Modbus function READ_COILS (0x01)

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_nb: number of bits to read (1 to 2000)
        :type bit_nb: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        if not (0 <= int(bit_addr) <= 65535):
            self.__debug_msg('read_coils(): bit_addr out of range')
            return None
        if not (1 <= int(bit_nb) <= 2000):
            self.__debug_msg('read_coils(): bit_nb out of range')
            return None
        if (int(bit_addr) + int(bit_nb)) > 65536:
            self.__debug_msg('read_coils(): read after ad 65535')
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_COILS, struct.pack('>HH', bit_addr, bit_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check min frame body size
        if len(f_body) < 2:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_coils(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        rx_byte_count = struct.unpack("B", f_body[0:1])[0]
        # frame with bits value -> bits[] list
        f_bits = bytearray(f_body[1:])
        # check rx_byte_count: match nb of bits request and check buffer size
        if not ((rx_byte_count >= int((bit_nb + 7) / 8)) and
                (rx_byte_count == len(f_bits))):
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_coils(): rx byte count mismatch')
            self.close()
            return None
        # allocate a bit_nb size list
        bits = [None] * bit_nb
        # fill bits list with bit items
        for i, item in enumerate(bits):
            bits[i] = bool(f_bits[int(i / 8)] >> (i % 8) & 0x01)
        # return bits list
        return bits

    def read_discrete_inputs(self, bit_addr, bit_nb=1):
        """Modbus function READ_DISCRETE_INPUTS (0x02)

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_nb: number of bits to read (1 to 2000)
        :type bit_nb: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        if not (0 <= int(bit_addr) <= 65535):
            self.__debug_msg('read_discrete_inputs(): bit_addr out of range')
            return None
        if not (1 <= int(bit_nb) <= 2000):
            self.__debug_msg('read_discrete_inputs(): bit_nb out of range')
            return None
        if (int(bit_addr) + int(bit_nb)) > 65536:
            self.__debug_msg('read_discrete_inputs(): read after ad 65535')
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_DISCRETE_INPUTS, struct.pack('>HH', bit_addr, bit_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check min frame body size
        if len(f_body) < 2:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_discrete_inputs(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        rx_byte_count = struct.unpack("B", f_body[0:1])[0]
        # frame with bits value -> bits[] list
        f_bits = bytearray(f_body[1:])
        # check rx_byte_count: match nb of bits request and check buffer size
        if not ((rx_byte_count >= int((bit_nb + 7) / 8)) and
                (rx_byte_count == len(f_bits))):
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_discrete_inputs(): rx byte count mismatch')
            self.close()
            return None
        # allocate a bit_nb size list
        bits = [None] * bit_nb
        # fill bits list with bit items
        for i, item in enumerate(bits):
            bits[i] = bool(f_bits[int(i / 8)] >> (i % 8) & 0x01)
        # return bits list
        return bits

    def read_holding_registers(self, reg_addr, reg_nb=1):
        """Modbus function READ_HOLDING_REGISTERS (0x03)

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_nb: number of registers to read (1 to 125)
        :type reg_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        if not (0 <= int(reg_addr) <= 65535):
            self.__debug_msg('read_holding_registers(): reg_addr out of range')
            return None
        if not (1 <= int(reg_nb) <= 125):
            self.__debug_msg('read_holding_registers(): reg_nb out of range')
            return None
        if (int(reg_addr) + int(reg_nb)) > 65536:
            self.__debug_msg('read_holding_registers(): read after ad 65535')
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_HOLDING_REGISTERS, struct.pack('>HH', reg_addr, reg_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check min frame body size
        if len(f_body) < 2:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_holding_registers(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        rx_byte_count = struct.unpack('B', f_body[0:1])[0]
        # frame with regs value
        f_regs = f_body[1:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if not ((rx_byte_count >= 2 * reg_nb) and
                (rx_byte_count == len(f_regs))):
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_holding_registers(): rx byte count mismatch')
            self.close()
            return None
        # allocate a reg_nb size list
        registers = [None] * reg_nb
        # fill registers list with register items
        for i, item in enumerate(registers):
            registers[i] = struct.unpack('>H', f_regs[i * 2:i * 2 + 2])[0]
        # return registers list
        return registers

    def read_input_registers(self, reg_addr, reg_nb=1):
        """Modbus function READ_INPUT_REGISTERS (0x04)

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_nb: number of registers to read (1 to 125)
        :type reg_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        if not (0x0000 <= int(reg_addr) <= 0xffff):
            self.__debug_msg('read_input_registers(): reg_addr out of range')
            return None
        if not (0x0001 <= int(reg_nb) <= 0x007d):
            self.__debug_msg('read_input_registers(): reg_nb out of range')
            return None
        if (int(reg_addr) + int(reg_nb)) > 0x10000:
            self.__debug_msg('read_input_registers(): read after ad 65535')
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.READ_INPUT_REGISTERS, struct.pack('>HH', reg_addr, reg_nb))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check min frame body size
        if len(f_body) < 2:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_input_registers(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        rx_byte_count = struct.unpack('B', f_body[0:1])[0]
        # frame with regs value
        f_regs = f_body[1:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if not ((rx_byte_count >= 2 * reg_nb) and
                (rx_byte_count == len(f_regs))):
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('read_input_registers(): rx byte count mismatch')
            self.close()
            return None
        # allocate a reg_nb size list
        registers = [None] * reg_nb
        # fill registers list with register items
        for i, item in enumerate(registers):
            registers[i] = struct.unpack('>H', f_regs[i * 2:i * 2 + 2])[0]
        # return registers list
        return registers

    def write_single_coil(self, bit_addr, bit_value):
        """Modbus function WRITE_SINGLE_COIL (0x05)

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_value: bit value to write
        :type bit_value: bool
        :returns: True if write ok or None if fail
        :rtype: bool or None
        """
        # check params
        if not (0 <= int(bit_addr) <= 65535):
            self.__debug_msg('write_single_coil(): bit_addr out of range')
            return None
        # build frame
        bit_value = 0xFF if bit_value else 0x00
        tx_buffer = self._mbus_frame(const.WRITE_SINGLE_COIL, struct.pack('>HBB', bit_addr, bit_value, 0))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check fix frame size
        if len(f_body) != 4:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('write_single_coil(): rx frame size error')
            self.close()
            return None
        # register extract
        (rx_bit_addr, rx_bit_value, rx_padding) = struct.unpack('>HBB', f_body[:4])
        # check bit write
        is_ok = (rx_bit_addr == bit_addr) and (rx_bit_value == bit_value)
        return True if is_ok else None

    def write_single_register(self, reg_addr, reg_value):
        """Modbus function WRITE_SINGLE_REGISTER (0x06)

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_value: register value to write
        :type reg_value: int
        :returns: True if write ok or None if fail
        :rtype: bool or None
        """
        # check params
        if not (0 <= int(reg_addr) <= 65535):
            self.__debug_msg('write_single_register(): reg_addr out of range')
            return None
        if not (0 <= int(reg_value) <= 65535):
            self.__debug_msg('write_single_register(): reg_value out of range')
            return None
        # build frame
        tx_buffer = self._mbus_frame(const.WRITE_SINGLE_REGISTER,
                                     struct.pack('>HH', reg_addr, reg_value))
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check fix frame size
        if len(f_body) != 4:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('write_single_register(): rx frame size error')
            self.close()
            return None
        # register extract
        rx_reg_addr, rx_reg_value = struct.unpack('>HH', f_body)
        # check register write
        is_ok = (rx_reg_addr == reg_addr) and (rx_reg_value == reg_value)
        return True if is_ok else None

    def write_multiple_coils(self, bits_addr, bits_value):
        """Modbus function WRITE_MULTIPLE_COILS (0x0F)

        :param bits_addr: bits address (0 to 65535)
        :type bits_addr: int
        :param bits_value: bits values to write
        :type bits_value: list
        :returns: True if write ok or None if fail
        :rtype: bool or None
        """
        # number of bits to write
        bits_nb = len(bits_value)
        # check params
        if not (0x0000 <= int(bits_addr) <= 0xffff):
            self.__debug_msg('write_multiple_coils(): bits_addr out of range')
            return None
        if not (0x0001 <= int(bits_nb) <= 0x07b0):
            self.__debug_msg('write_multiple_coils(): number of bits out of range')
            return None
        if (int(bits_addr) + int(bits_nb)) > 0x10000:
            self.__debug_msg('write_multiple_coils(): write after ad 65535')
            return None
        # build frame
        # format bits value string
        bits_val_str = b''
        # allocate bytes list
        b_size = int(bits_nb / 8)
        b_size += 1 if (bits_nb % 8) else 0
        bytes_l = [0] * b_size
        # populate bytes list with bits_value
        for i, item in enumerate(bits_value):
            if item:
                byte_i = int(i/8)
                bytes_l[byte_i] = set_bit(bytes_l[byte_i], i % 8)
        # format bits_val_str
        for byte in bytes_l:
            bits_val_str += struct.pack('B', byte)
        bytes_nb = len(bits_val_str)
        # format modbus frame body
        body = struct.pack('>HHB', bits_addr, bits_nb, bytes_nb) + bits_val_str
        tx_buffer = self._mbus_frame(const.WRITE_MULTIPLE_COILS, body)
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check fix frame size
        if len(f_body) != 4:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('write_multiple_coils(): rx frame size error')
            self.close()
            return None
        # register extract
        (rx_bit_addr, rx_bit_nb) = struct.unpack('>HH', f_body[:4])
        # check regs write
        is_ok = (rx_bit_addr == bits_addr)
        return True if is_ok else None

    def write_multiple_registers(self, regs_addr, regs_value):
        """Modbus function WRITE_MULTIPLE_REGISTERS (0x10)

        :param regs_addr: registers address (0 to 65535)
        :type regs_addr: int
        :param regs_value: registers values to write
        :type regs_value: list
        :returns: True if write ok or None if fail
        :rtype: bool or None
        """
        # number of registers to write
        regs_nb = len(regs_value)
        # check params
        if not (0x0000 <= int(regs_addr) <= 0xffff):
            self.__debug_msg('write_multiple_registers(): regs_addr out of range')
            return None
        if not (0x0001 <= int(regs_nb) <= 0x007b):
            self.__debug_msg('write_multiple_registers(): number of registers out of range')
            return None
        if (int(regs_addr) + int(regs_nb)) > 0x10000:
            self.__debug_msg('write_multiple_registers(): write after ad 65535')
            return None
        # build frame
        # format reg value string
        regs_val_str = b""
        for reg in regs_value:
            # check current register value
            if not (0 <= int(reg) <= 0xffff):
                self.__debug_msg('write_multiple_registers(): regs_value out of range')
                return None
            # pack register for build frame
            regs_val_str += struct.pack('>H', reg)
        bytes_nb = len(regs_val_str)
        # format modbus frame body
        body = struct.pack('>HHB', regs_addr, regs_nb, bytes_nb) + regs_val_str
        tx_buffer = self._mbus_frame(const.WRITE_MULTIPLE_REGISTERS, body)
        # send request
        s_send = self._send_mbus(tx_buffer)
        # check error
        if not s_send:
            return None
        # receive
        f_body = self._recv_mbus()
        # check error
        if not f_body:
            return None
        # check fix frame size
        if len(f_body) != 4:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('write_multiple_registers(): rx frame size error')
            self.close()
            return None
        # register extract
        (rx_reg_addr, rx_reg_nb) = struct.unpack('>HH', f_body[:4])
        # check regs write
        is_ok = (rx_reg_addr == regs_addr)
        return True if is_ok else None

    def _can_read(self):
        """Wait data available for socket read

        :returns: True if data available or None if timeout or socket error
        :rtype: bool or None
        """
        if self.__sock is None:
            return None
        if select.select([self.__sock], [], [], self.__timeout)[0]:
            return True
        else:
            self.__last_error = const.MB_TIMEOUT_ERR
            self.__debug_msg('timeout error')
            self.close()
            return None

    def _send(self, data):
        """Send data over current socket

        :param data: registers value to write
        :type data: str (Python2) or class bytes (Python3)
        :returns: True if send ok or None if error
        :rtype: bool or None
        """
        # check link
        if self.__sock is None:
            self.__debug_msg('call _send on close socket')
            return None
        # send
        data_l = len(data)
        try:
            send_l = self.__sock.send(data)
        except socket.error:
            send_l = None
        # handle send error
        if (send_l is None) or (send_l != data_l):
            self.__last_error = const.MB_SEND_ERR
            self.__debug_msg('_send error')
            self.close()
            return None
        else:
            return send_l

    def _recv(self, max_size):
        """Receive data over current socket

        :param max_size: number of bytes to receive
        :type max_size: int
        :returns: receive data or None if error
        :rtype: str (Python2) or class bytes (Python3) or None
        """
        # wait for read
        if not self._can_read():
            self.close()
            return None
        # recv
        try:
            r_buffer = self.__sock.recv(max_size)
        except socket.error:
            r_buffer = None
        # handle recv error
        if not r_buffer:
            self.__last_error = const.MB_RECV_ERR
            self.__debug_msg('_recv error')
            self.close()
            return None
        return r_buffer

    def _send_mbus(self, frame):
        """Send modbus frame

        :param frame: modbus frame to send (with MBAP for TCP/CRC for RTU)
        :type frame: str (Python2) or class bytes (Python3)
        :returns: number of bytes send or None if error
        :rtype: int or None
        """
        # for auto_open mode, check TCP and open if need
        if self.__auto_open and not self.is_open():
            self.open()
        # send request
        bytes_send = self._send(frame)
        if bytes_send:
            if self.__debug:
                self._pretty_dump('Tx', frame)
            return bytes_send
        else:
            return None

    def _recv_mbus(self):
        """Receive a modbus frame

        :returns: modbus frame body or None if error
        :rtype: str (Python2) or class bytes (Python3) or None
        """
        # receive
        # modbus TCP receive
        if self.__mode == const.MODBUS_TCP:
            # 7 bytes header (mbap)
            rx_buffer = self._recv(7)
            # check recv
            if not (rx_buffer and len(rx_buffer) == 7):
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg('_recv MBAP error')
                self.close()
                return None
            rx_frame = rx_buffer
            # decode header
            (rx_hd_tr_id, rx_hd_pr_id,
             rx_hd_length, rx_hd_unit_id) = struct.unpack('>HHHB', rx_frame)
            # check header
            if not ((rx_hd_tr_id == self.__hd_tr_id) and
                    (rx_hd_pr_id == 0) and
                    (rx_hd_length < 256) and
                    (rx_hd_unit_id == self.__unit_id)):
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg('MBAP format error')
                if self.__debug:
                    rx_frame += self._recv(rx_hd_length - 1)
                    self._pretty_dump('Rx', rx_frame)
                self.close()
                return None
            # end of frame
            rx_buffer = self._recv(rx_hd_length - 1)
            if not (rx_buffer and
                    (len(rx_buffer) == rx_hd_length - 1) and
                    (len(rx_buffer) >= 2)):
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg('_recv frame body error')
                self.close()
                return None
            rx_frame += rx_buffer
            # dump frame
            if self.__debug:
                self._pretty_dump('Rx', rx_frame)
            # body decode
            rx_bd_fc = struct.unpack('B', rx_buffer[0:1])[0]
            f_body = rx_buffer[1:]
        # modbus RTU receive
        elif self.__mode == const.MODBUS_RTU:
            # receive modbus RTU frame (max size is 256 bytes)
            rx_buffer = self._recv(256)
            # on _recv error
            if not rx_buffer:
                return None
            rx_frame = rx_buffer
            # dump frame
            if self.__debug:
                self._pretty_dump('Rx', rx_frame)
            # RTU frame min size is 5 bytes
            if len(rx_buffer) < 5:
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg('short frame error')
                self.close()
                return None
            # check CRC
            if not self._crc_is_ok(rx_frame):
                self.__last_error = const.MB_CRC_ERR
                self.__debug_msg('CRC error')
                self.close()
                return None
            # body decode
            (rx_unit_id, rx_bd_fc) = struct.unpack("BB", rx_frame[:2])
            # check
            if not (rx_unit_id == self.__unit_id):
                self.__last_error = const.MB_RECV_ERR
                self.__debug_msg('unit ID mismatch error')
                self.close()
                return None
            # format f_body: remove unit ID, function code and CRC 2 last bytes
            f_body = rx_frame[2:-2]
        # for auto_close mode, close socket after each request
        if self.__auto_close:
            self.close()
        # check except
        if rx_bd_fc > 0x80:
            # except code
            exp_code = struct.unpack('B', f_body[0:1])[0]
            self.__last_error = const.MB_EXCEPT_ERR
            self.__last_except = exp_code
            self.__debug_msg('except (code ' + str(exp_code) + ')')
            return None
        else:
            # return
            return f_body

    def _mbus_frame(self, fc, body):
        """Build modbus frame (add MBAP for Modbus/TCP, slave AD + CRC for RTU)

        :param fc: modbus function code
        :type fc: int
        :param body: modbus frame body
        :type body: str (Python2) or class bytes (Python3)
        :returns: modbus frame
        :rtype: str (Python2) or class bytes (Python3)
        """
        # build frame body
        f_body = struct.pack('B', fc) + body
        # modbus/TCP
        if self.__mode == const.MODBUS_TCP:
            # build frame ModBus Application Protocol header (mbap)
            self.__hd_tr_id = random.randint(0, 65535)
            tx_hd_pr_id = 0
            tx_hd_length = len(f_body) + 1
            f_mbap = struct.pack('>HHHB', self.__hd_tr_id, tx_hd_pr_id,
                                 tx_hd_length, self.__unit_id)
            return f_mbap + f_body
        # modbus RTU
        elif self.__mode == const.MODBUS_RTU:
            # format [slave addr(unit_id)]frame_body[CRC16]
            slave_ad = struct.pack('B', self.__unit_id)
            return self._add_crc(slave_ad + f_body)

    def _pretty_dump(self, label, data):
        """Print modbus/TCP frame ('[header]body')
        or RTU ('body[CRC]') on stdout

        :param label: modbus function code
        :type label: str
        :param data: modbus frame
        :type data: str (Python2) or class bytes (Python3)
        """
        # split data string items to a list of hex value
        dump = ['%02X' % c for c in bytearray(data)]
        # format for TCP or RTU
        if self.__mode == const.MODBUS_TCP:
            if len(dump) > 6:
                # [MBAP] ...
                dump[0] = '[' + dump[0]
                dump[6] += ']'
        elif self.__mode == const.MODBUS_RTU:
            if len(dump) > 4:
                # ... [CRC]
                dump[-2] = '[' + dump[-2]
                dump[-1] += ']'
        # print result
        print(label)
        s = ''
        for i in dump:
            s += i + ' '
        print(s)

    def _add_crc(self, frame):
        """Add CRC to modbus frame (for RTU mode)

        :param frame: modbus RTU frame
        :type frame: str (Python2) or class bytes (Python3)
        :returns: modbus RTU frame with CRC
        :rtype: str (Python2) or class bytes (Python3)
        """
        crc = struct.pack('<H', crc16(frame))
        return frame + crc

    def _crc_is_ok(self, frame):
        """Check the CRC of modbus RTU frame

        :param frame: modbus RTU frame with CRC
        :type frame: str (Python2) or class bytes (Python3)
        :returns: status CRC (True for valid)
        :rtype: bool
        """
        return crc16(frame) == 0

    def __debug_msg(self, msg):
        """Print debug message if debug mode is on

        :param msg: debug message
        :type msg: str
        """
        if self.__debug:
            print(msg)
