# Python module: ModbusClient class (Client ModBus/TCP)

from . import constants as const
from .utils import set_bit
import re
import socket
import select
import struct
import random


class ModbusClient(object):

    """Modbus TCP client"""

    def __init__(self, host='localhost', port=502, unit_id=1, timeout=30.0,
                 debug=False, auto_open=True, auto_close=False):
        """Constructor

        :param host: hostname or IPv4/IPv6 address server address
        :type host: str
        :param port: TCP port number
        :type port: int
        :param unit_id: unit ID
        :type unit_id: int
        :param timeout: socket timeout in seconds
        :type timeout: float
        :param debug: debug state
        :type debug: bool
        :param auto_open: auto TCP connect
        :type auto_open: bool
        :param auto_close: auto TCP close)
        :type auto_close: bool
        :return: Object ModbusClient
        :rtype: ModbusClient
        :raises ValueError: if a set parameter value is incorrect
        """
        # public property (update with constructor args)
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.debug = debug
        self.auto_open = auto_open
        self.auto_close = auto_close
        # private
        self._sock = None                   # socket
        self._hd_tr_id = 0                  # MBAP transaction ID
        self._version = const.VERSION       # this package version number
        self._last_error = const.MB_NO_ERR  # last error code
        self._last_except = const.EXP_NONE  # last expect code

    @property
    def version(self):
        """Get package version

        :return: current version of the package (like "0.0.1")
        :rtype: str
        """
        return self._version

    @property
    def last_error(self):
        """Get last error code

        :return: last error code
        :rtype: int
        """
        return self._last_error

    @property
    def last_error_as_txt(self):
        """Get last error as human readable text

        :return: last error as string
        :rtype: str
        """
        return const.MB_ERR_TXT.get(self._last_error, 'unknown error')

    @property
    def last_except(self):
        """Get last exception code

        :return: last exception code
        :rtype: int
        """
        return self._last_except

    @property
    def last_except_as_txt(self):
        """Get last exception code as short human readable text

        :return: short human readable text to describe last exception
        :rtype: str
        """
        default_str = 'unreferenced exception 0x%X' % self._last_except
        return const.EXP_TXT.get(self._last_except, default_str)

    @property
    def last_except_as_full_txt(self):
        """Get last exception code as human readable text (verbose version)

        :return: verbose human readable text to describe last exception
        :rtype: str
        """
        default_str = 'unreferenced exception 0x%X' % self._last_except
        return const.EXP_DETAILS.get(self._last_except, default_str)

    @property
    def host(self):
        return self._host

    @host.setter
    def host(self, value):
        """Get or set host (IPv4/IPv6 or hostname like 'plc.domain.net')

        :param hostname: hostname or IPv4/IPv6 address
        :type hostname: str
        """
        # check type
        if type(value) is not str:
            raise TypeError('host must be a str')
        # IPv4 valid address ?
        try:
            socket.inet_pton(socket.AF_INET, value)
            self._host = value
            return
        except socket.error:
            pass
        # IPv6 valid address ?
        try:
            socket.inet_pton(socket.AF_INET6, value)
            self._host = value
            return
        except socket.error:
            pass
        # valid hostname ?
        if re.match(r'^[a-z][a-z0-9.\-]+$', value):
            self._host = value
            return
        # if can't be set
        raise ValueError('host can\'t be set (not a valid IP address or hostname)')

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, value):
        """Get or set TCP port

        :param value: TCP port number or None for get value
        :type value: int
        """
        # check type
        if type(value) is not int:
            raise TypeError('port must be an int')
        # check validity
        if 0 < value < 65536:
            self._port = value
            return
        # if can't be set
        raise ValueError('port can\'t be set (valid if 0 < port < 65536)')


    @property
    def unit_id(self):
        return self._unit_id

    @unit_id.setter
    def unit_id(self, value):
        """Get or set unit ID field

        :param unit_id: unit ID (0 to 255)
        :type unit_id: int
        """
        # check type
        if type(value) is not int:
            raise TypeError('unit_id must be an int')
        # check validity
        if 0 <= int(value) < 256:
            self._unit_id = int(value)
            return
        # if can't be set
        raise ValueError('unit_id can\'t be set (valid if 0 <= unit_id < 256)')

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        """Get or set timeout field

        :param value: socket timeout in seconds
        :type value: float
        """
        # enforce type
        value = float(value)
        # check validity
        if 0 < value < 3600:
            self._timeout = value
            return
        # if can't be set
        raise ValueError('timeout can\'t be set (valid if 0 < timeout < 3600)')

    @property
    def debug(self):
        return self._debug

    @debug.setter
    def debug(self, value):
        """Get or set debug mode

        :param value: debug state
        :type value: bool
        """
        # enforce type
        self._debug = bool(value)

    @property
    def auto_open(self):
        return self._auto_open

    @auto_open.setter
    def auto_open(self, value):
        """Get or set automatic TCP connect mode

        :param state: auto_open state or None for get value
        :type state: bool or None
        """
        # enforce type
        self._auto_open = bool(value)

    @property
    def auto_close(self):
        return self._auto_close

    @auto_close.setter
    def auto_close(self, value):
        """Get or set automatic TCP close mode (after each request)

        :param state: auto_close state or None for get value
        :type state: bool or None
        """
        # enforce type
        self._auto_close = bool(value)

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
        for res in socket.getaddrinfo(self._host, self._port,
                                      socket.AF_UNSPEC, socket.SOCK_STREAM):
            af, sock_type, proto, canon_name, sa = res
            try:
                self._sock = socket.socket(af, sock_type, proto)
            except socket.error:
                self._sock = None
                continue
            try:
                self._sock.settimeout(self._timeout)
                self._sock.connect(sa)
            except socket.error:
                self._sock.close()
                self._sock = None
                continue
            break
        # check connect status
        if self._sock is None:
            self._last_error = const.MB_CONNECT_ERR
            self._debug_msg('connect error')
            return False
        else:
            return True

    def is_open(self):
        """Get status of TCP connection

        :returns: status (True for open)
        :rtype: bool
        """
        return self._sock is not None

    def close(self):
        """Close TCP connection

        :returns: close status (True for close/None if already close)
        :rtype: bool or None
        """
        if self._sock:
            self._sock.close()
            self._sock = None
            return True
        else:
            return None

    def custom_request(self, pdu):
        """Send a custom modbus request

        :param pdu: a modbus PDU (protocol data unit)
        :type pdu: str (Python2) or class bytes (Python3)
        :returns: modbus frame PDU or None if error
        :rtype: str (Python2) or class bytes (Python3) or None
        """
        # send custom request
        if not self._send_pdu(pdu):
            return None
        # receive
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # check min frame body size
        if len(rx_pdu) < 2:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('custom_request(): rx frame under min size')
            self.close()
            return None
        # return bits list
        return rx_pdu

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
        if not (0 <= int(bit_addr) <= 0xffff):
            self._debug_msg('read_coils(): bit_addr out of range')
            return None
        if not (1 <= int(bit_nb) <= 2000):
            self._debug_msg('read_coils(): bit_nb out of range')
            return None
        if (int(bit_addr) + int(bit_nb)) > 0x10000:
            self._debug_msg('read_coils(): read after ad 65535')
            return None
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', const.READ_COILS, bit_addr, bit_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check min frame body size
        if len(rx_pdu) < 3:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_coils(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with bits value -> bits[] list
        f_bits = rx_pdu[2:]
        # check rx_byte_count: match nb of bits request and check buffer size
        if not ((byte_count >= int((bit_nb + 7) / 8)) and
                (byte_count == len(f_bits))):
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_coils(): rx byte count mismatch')
            self.close()
            return None
        # allocate a bit_nb size list
        bits = [None] * bit_nb
        # fill bits list with bit items
        for i, _ in enumerate(bits):
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
        if not (0 <= int(bit_addr) <= 0xffff):
            self._debug_msg('read_discrete_inputs(): bit_addr out of range')
            return None
        if not (1 <= int(bit_nb) <= 2000):
            self._debug_msg('read_discrete_inputs(): bit_nb out of range')
            return None
        if (int(bit_addr) + int(bit_nb)) > 0x10000:
            self._debug_msg('read_discrete_inputs(): read after ad 65535')
            return None
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', const.READ_DISCRETE_INPUTS, bit_addr, bit_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check min frame body size
        if len(rx_pdu) < 3:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_discrete_inputs(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with bits value -> bits[] list
        f_bits = rx_pdu[2:]
        # check rx_byte_count: match nb of bits request and check buffer size
        if not ((byte_count >= int((bit_nb + 7) / 8)) and
                (byte_count == len(f_bits))):
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_discrete_inputs(): rx byte count mismatch')
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
        if not (0 <= int(reg_addr) <= 0xffff):
            self._debug_msg('read_holding_registers(): reg_addr out of range')
            return None
        if not (1 <= int(reg_nb) <= 125):
            self._debug_msg('read_holding_registers(): reg_nb out of range')
            return None
        if (int(reg_addr) + int(reg_nb)) > 0x10000:
            self._debug_msg('read_holding_registers(): read after ad 65535')
            return None
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', const.READ_HOLDING_REGISTERS, reg_addr, reg_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check min frame body size
        if len(rx_pdu) < 3:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_holding_registers(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with regs value
        f_regs = rx_pdu[2:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if not ((byte_count >= 2 * reg_nb) and
                (byte_count == len(f_regs))):
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_holding_registers(): rx byte count mismatch')
            self.close()
            return None
        # allocate a reg_nb size list
        registers = [None] * reg_nb
        # fill registers list with register items
        for i, _ in enumerate(registers):
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
            self._debug_msg('read_input_registers(): reg_addr out of range')
            return None
        if not (0x0001 <= int(reg_nb) <= 125):
            self._debug_msg('read_input_registers(): reg_nb out of range')
            return None
        if (int(reg_addr) + int(reg_nb)) > 0x10000:
            self._debug_msg('read_input_registers(): read after ad 65535')
            return None
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', const.READ_INPUT_REGISTERS, reg_addr, reg_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check min frame body size
        if len(rx_pdu) < 3:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_input_registers(): rx frame under min size')
            self.close()
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with regs value
        f_regs = rx_pdu[2:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if not ((byte_count >= 2 * reg_nb) and
                (byte_count == len(f_regs))):
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('read_input_registers(): rx byte count mismatch')
            self.close()
            return None
        # allocate a reg_nb size list
        registers = [None] * reg_nb
        # fill registers list with register items
        for i, _ in enumerate(registers):
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
        if not (0 <= int(bit_addr) <= 0xffff):
            self._debug_msg('write_single_coil(): bit_addr out of range')
            return None
        # format "bit value" field
        bit_value = 0xff if bit_value else 0x00
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHBB', const.WRITE_SINGLE_COIL, bit_addr, bit_value, 0)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check fix frame size
        if len(rx_pdu) != 5:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('write_single_coil(): rx frame size error')
            self.close()
            return None
        # register extract
        (ret_bit_addr, ret_bit_value, _) = struct.unpack('>HBB', rx_pdu[1:5])
        # check bit write
        is_ok = (ret_bit_addr == bit_addr) and (ret_bit_value == bit_value)
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
        if not (0 <= int(reg_addr) <= 0xffff):
            self._debug_msg('write_single_register(): reg_addr out of range')
            return None
        if not (0 <= int(reg_value) <= 0xffff):
            self._debug_msg('write_single_register(): reg_value out of range')
            return None
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', const.WRITE_SINGLE_REGISTER, reg_addr, reg_value)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check fix frame size
        if len(rx_pdu) != 5:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('write_single_register(): rx frame size error')
            self.close()
            return None
        # register extract
        ret_reg_addr, ret_reg_value = struct.unpack('>HH', rx_pdu[1:5])
        # check register write
        is_ok = (ret_reg_addr == reg_addr) and (ret_reg_value == reg_value)
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
            self._debug_msg('write_multiple_coils(): bits_addr out of range')
            return None
        if not (0x0001 <= int(bits_nb) <= 1968):
            self._debug_msg('write_multiple_coils(): number of bits out of range')
            return None
        if (int(bits_addr) + int(bits_nb)) > 0x10000:
            self._debug_msg('write_multiple_coils(): write after ad 65535')
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
        # build pdu and send request
        tx_pdu = struct.pack('>BHHB', const.WRITE_MULTIPLE_COILS, bits_addr, bits_nb, bytes_nb) + bits_val_str
        if not self._send_pdu(tx_pdu):
            return None
        # receive
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check fix frame size
        if len(rx_pdu) != 5:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('write_multiple_coils(): rx frame size error')
            self.close()
            return None
        # register extract
        (ret_bit_addr, _) = struct.unpack('>HH', rx_pdu[1:5])
        # check regs write
        is_ok = (ret_bit_addr == bits_addr)
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
            self._debug_msg('write_multiple_registers(): regs_addr out of range')
            return None
        if not (0x0001 <= int(regs_nb) <= 123):
            self._debug_msg('write_multiple_registers(): number of registers out of range')
            return None
        if (int(regs_addr) + int(regs_nb)) > 0x10000:
            self._debug_msg('write_multiple_registers(): write after ad 65535')
            return None
        # build frame
        # format reg value string
        regs_val_str = b''
        for reg in regs_value:
            # check current register value
            if not (0 <= int(reg) <= 0xffff):
                self._debug_msg('write_multiple_registers(): regs_value out of range')
                return None
            # pack register for build frame
            regs_val_str += struct.pack('>H', reg)
        bytes_nb = len(regs_val_str)
        # build pdu and send request
        tx_pdu = struct.pack('>BHHB', const.WRITE_MULTIPLE_REGISTERS, regs_addr, regs_nb, bytes_nb) + regs_val_str
        if not self._send_pdu(tx_pdu):
            return None
        # receive
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # enforce bytearray for py2
        rx_pdu = bytearray(rx_pdu)
        # check fix frame size
        if len(rx_pdu) != 5:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('write_multiple_registers(): rx frame size error')
            self.close()
            return None
        # register extract
        (ret_reg_addr, _) = struct.unpack('>HH', rx_pdu[1:5])
        # check regs write
        is_ok = (ret_reg_addr == regs_addr)
        return True if is_ok else None

    def _can_read(self):
        """Wait data available for socket read

        :returns: True if data available or None if timeout or socket error
        :rtype: bool or None
        """
        if self._sock is None:
            return None
        if select.select([self._sock], [], [], self._timeout)[0]:
            return True
        else:
            self._last_error = const.MB_TIMEOUT_ERR
            self._debug_msg('timeout error')
            self.close()
            return None

    def _send(self, frame):
        """Send frame over current socket

        :param frame: modbus frame to send (MBAP + PDU)
        :type frame: str (Python2) or class bytes (Python3)
        :returns: True on success
        :rtype: bool
        """
        # check link
        if self._sock is None:
            self._last_error = const.MB_SOCK_CLOSE_ERR
            self._debug_msg('call _send on close socket')
            return False
        # send
        try:
            self._sock.send(frame)
        except socket.error:
            self._last_error = const.MB_SEND_ERR
            self._debug_msg('_send error')
            self.close()
            return False
        return True

    def _send_pdu(self, pdu):
        """Convert modbus PDU to frame and send it

        :param pdu: modbus frame PDU
        :type pdu: str (Python2) or class bytes (Python3)
        :returns: True on success
        :rtype: bool
        """
        # for auto_open mode, check TCP and open if need
        if self._auto_open and not self.is_open():
            self.open()
        # add headers to pdu and send frame
        tx_frame = self._add_mbap(pdu)
        if not self._send(tx_frame):
            return False
        # debug
        if self._debug:
            self._pretty_dump('Tx', tx_frame)
        return True

    def _recv(self, size):
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
            r_buffer = self._sock.recv(size)
        except socket.error:
            r_buffer = None
        # handle recv error
        if not r_buffer:
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('_recv error')
            self.close()
            return None
        return r_buffer

    def _recv_all(self, size):
        """Receive data over current socket, loop until all bytes is receive (avoid TCP frag)

        :param size: number of bytes to receive
        :type size: int
        :returns: receive data or None if error
        :rtype: str (Python2) or class bytes (Python3) or None
        """
        r_buffer = bytes()
        while len(r_buffer) < size:
            r_packet = self._recv(size - len(r_buffer))
            if not r_packet:
                return None
            r_buffer += r_packet
        return r_buffer

    def _recv_pdu(self):
        """Receive a modbus frame

        Modbus/TCP: first read mbap head and

        :returns: modbus frame PDU or None if error
        :rtype: str (Python2) or class bytes (Python3) or None
        """
        # receive
        # 7 bytes header (mbap)
        rx_mbap = self._recv_all(7)
        # check recv
        if not (rx_mbap and len(rx_mbap) == 7):
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('_recv MBAP error')
            self.close()
            return None
        # decode header
        (rx_hd_tr_id, rx_hd_pr_id,
         rx_hd_length, rx_hd_unit_id) = struct.unpack('>HHHB', rx_mbap)
        # check header
        if not ((rx_hd_tr_id == self._hd_tr_id) and
                (rx_hd_pr_id == 0) and
                (rx_hd_length < 256) and
                (rx_hd_unit_id == self._unit_id)):
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('MBAP format error')
            if self._debug:
                self._pretty_dump('Rx', rx_mbap)
            self.close()
            return None
        # end of frame
        rx_pdu = self._recv_all(rx_hd_length - 1)
        if not (rx_pdu and
                (len(rx_pdu) == rx_hd_length - 1) and
                (len(rx_pdu) >= 2)):
            self._last_error = const.MB_RECV_ERR
            self._debug_msg('_recv frame body error')
            self.close()
            return None
        # dump frame
        if self._debug:
            self._pretty_dump('Rx', rx_mbap + rx_pdu)
        # body decode
        rx_fc = struct.unpack('B', rx_pdu[0:1])[0]
        # for auto_close mode, close socket after each request
        if self._auto_close:
            self.close()
        # check except status
        if rx_fc >= 0x80:
            exp_code = struct.unpack('B', rx_pdu[1:2])[0]
            self._last_error = const.MB_EXCEPT_ERR
            self._last_except = exp_code
            self._debug_msg('except (code ' + str(exp_code) + ')')
            return None
        else:
            return rx_pdu

    def _add_mbap(self, pdu):
        """Return full modbus frame with MBAP (modbus application protocol header)

        :param pdu: modbus PDU (protocol data unit)
        :type pdu: str (Python2) or class bytes (Python3)
        :returns: full modbus frame
        :rtype: str (Python2) or class bytes (Python3)
        """
        # build MBAP
        self._hd_tr_id = random.randint(0, 65535)
        tx_hd_pr_id = 0
        tx_hd_length = len(pdu) + 1
        mbap = struct.pack('>HHHB', self._hd_tr_id, tx_hd_pr_id,
                            tx_hd_length, self._unit_id)
        # full modbus/TCP frame = [MBAP]PDU
        return mbap + pdu

    def _debug_msg(self, msg):
        """Print debug message if debug mode is on

        :param msg: debug message
        :type msg: str
        """
        if self._debug:
            print(msg)

    def _pretty_dump(self, label, frame):
        """Dump a modbus frame

        modbus/TCP format: [MBAP] PDU

        :param label: head label
        :type label: str
        :param data: modbus frame
        :type data: str (Python2) or class bytes (Python3)
        """
        # split data string items to a list of hex value
        dump = ['%02X' % c for c in bytearray(frame)]
        # format message
        dump_mbap = ' '.join(dump[0:7])
        dump_pdu = ' '.join(dump[7:])
        msg = '[%s] %s' % (dump_mbap, dump_pdu)
        # print result
        print(label)
        print(msg)
