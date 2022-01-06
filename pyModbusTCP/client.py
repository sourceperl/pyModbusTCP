# Python module: ModbusClient class (Client ModBus/TCP)

from .constants import READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS, \
    WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS, WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, \
    EXP_TXT, EXP_DETAILS, EXP_NONE, \
    MB_ERR_TXT, MB_NO_ERR, MB_SEND_ERR, MB_RECV_ERR, MB_TIMEOUT_ERR, MB_EXCEPT_ERR, MB_CONNECT_ERR, \
    MB_SOCK_CLOSE_ERR, VERSION
from .utils import byte_length, set_bit, valid_host
import socket
import select
import struct
import random


class ModbusClient(object):
    """Modbus TCP client"""

    def __init__(self, host='localhost', port=502, unit_id=1, timeout=30.0,
                 debug=False, auto_open=True, auto_close=False):
        """Constructor.

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
        # private
        # internal variables
        self._host = None
        self._port = None
        self._unit_id = None
        self._timeout = None
        self._debug = None
        self._auto_open = None
        self._auto_close = None
        self._sock = None  # socket
        self._transaction_id = 0  # MBAP transaction ID
        self._version = VERSION  # this package version number
        self._last_error = MB_NO_ERR  # last error code
        self._last_except = EXP_NONE  # last except code
        # public
        # constructor arguments: validate them with property setters
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.debug = debug
        self.auto_open = auto_open
        self.auto_close = auto_close

    @property
    def version(self):
        """Return the current package version as a str."""
        return self._version

    @property
    def last_error(self):
        """Last error code."""
        return self._last_error

    @property
    def last_error_as_txt(self):
        """Human-readable text that describe last error."""
        return MB_ERR_TXT.get(self._last_error, 'unknown error')

    @property
    def last_except(self):
        """Return the last modbus exception code."""
        return self._last_except

    @property
    def last_except_as_txt(self):
        """Short human-readable text that describe last modbus exception."""
        default_str = 'unreferenced exception 0x%X' % self._last_except
        return EXP_TXT.get(self._last_except, default_str)

    @property
    def last_except_as_full_txt(self):
        """Verbose human-readable text that describe last modbus exception."""
        default_str = 'unreferenced exception 0x%X' % self._last_except
        return EXP_DETAILS.get(self._last_except, default_str)

    @property
    def host(self):
        """Get or set the server to connect to.

        This can be any string with a valid IPv4 / IPv6 address or hostname.
        Setting host to a new value will close the current socket.
        """
        return self._host

    @host.setter
    def host(self, value):
        # check type
        if type(value) is not str:
            raise TypeError('host must be a str')
        # check value
        if valid_host(value):
            if self._host != value:
                self.close()
                self._host = value
            return
        # can't be set
        raise ValueError('host can\'t be set (not a valid IP address or hostname)')

    @property
    def port(self):
        """Get or set the current TCP port (default is 502).

        Setting port to a new value will close the current socket.
        """
        return self._port

    @port.setter
    def port(self, value):
        # check type
        if type(value) is not int:
            raise TypeError('port must be an int')
        # check validity
        if 0 < value < 65536:
            if self._port != value:
                self.close()
                self._port = value
            return
        # can't be set
        raise ValueError('port can\'t be set (valid if 0 < port < 65536)')

    @property
    def unit_id(self):
        """Get or set the modbus unit identifier (default is 1).

        Any int from 0 to 255 is valid.
        """
        return self._unit_id

    @unit_id.setter
    def unit_id(self, value):
        # check type
        if type(value) is not int:
            raise TypeError('unit_id must be an int')
        # check validity
        if 0 <= value <= 255:
            self._unit_id = value
            return
        # can't be set
        raise ValueError('unit_id can\'t be set (valid from 0 to 255)')

    @property
    def timeout(self):
        """Get or set requests timeout (default is 30 seconds).

        The argument may be a floating point number for sub-second precision.
        """
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        # enforce type
        value = float(value)
        # check validity
        if 0 < value < 3600:
            self._timeout = value
            return
        # can't be set
        raise ValueError('timeout can\'t be set (valid between 0 and 3600)')

    @property
    def debug(self):
        """Get or set the debug flag (True = turn on)."""
        return self._debug

    @debug.setter
    def debug(self, value):
        # enforce type
        self._debug = bool(value)

    @property
    def auto_open(self):
        """Get or set automatic TCP connect mode (True = turn on)."""
        return self._auto_open

    @auto_open.setter
    def auto_open(self, value):
        # enforce type
        self._auto_open = bool(value)

    @property
    def auto_close(self):
        """Get or set automatic TCP close after each request mode (True = turn on)."""
        return self._auto_close

    @auto_close.setter
    def auto_close(self, value):
        # enforce type
        self._auto_close = bool(value)

    @property
    def is_open(self):
        """Get current status of the TCP connection (True = open)."""
        return self._sock is not None

    def open(self):
        """Connect to modbus server (open TCP connection).

        :returns: connect status (True on success)
        :rtype: bool
        """
        # call open() on an already open socket, reset it
        if self.is_open:
            self.close()
        # init socket and connect
        # list available sockets on the target host/port
        # AF_xxx : AF_INET -> IPv4, AF_INET6 -> IPv6,
        #          AF_UNSPEC -> IPv6 (priority on some system) or 4
        # list available socket on target host
        for res in socket.getaddrinfo(self.host, self.port,
                                      socket.AF_UNSPEC, socket.SOCK_STREAM):
            af, sock_type, proto, canon_name, sa = res
            try:
                self._sock = socket.socket(af, sock_type, proto)
            except socket.error:
                self._sock = None
                continue
            try:
                self._sock.settimeout(self.timeout)
                self._sock.connect(sa)
            except socket.error:
                self._sock.close()
                self._sock = None
                continue
            break
        # check connect status
        if self._sock is None:
            self._last_error = MB_CONNECT_ERR
            self._debug_msg('connect error')
            return False
        else:
            return True

    def close(self):
        """Close current TCP connection."""
        if self._sock:
            self._sock.close()
            self._sock = None

    def custom_request(self, pdu):
        """Send a custom modbus request.

        :param pdu: a modbus PDU (protocol data unit)
        :type pdu: str (Python2) or class bytes (Python3)
        :returns: modbus frame PDU or None if error
        :rtype: bytearray or None
        """
        # send custom pdu, return None on error
        if not self._send_pdu(pdu):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu()
        # check error
        if not rx_pdu:
            return None
        # return the rx pdu
        return rx_pdu

    def read_coils(self, bit_addr, bit_nb=1):
        """Modbus function READ_COILS (0x01).

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_nb: number of bits to read (1 to 2000)
        :type bit_nb: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        if not 0 <= int(bit_addr) <= 0xffff:
            raise ValueError('bit_addr out of range (valid from 0 to 65535)')
        if not 1 <= int(bit_nb) <= 2000:
            raise ValueError('bit_nb out of range (valid from 1 to 2000)')
        if int(bit_addr) + int(bit_nb) > 0x10000:
            raise ValueError('read after end of modbus address space')
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', READ_COILS, bit_addr, bit_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=3)
        # check error
        if not rx_pdu:
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with bits value -> bits[] list
        pdu_coils_part = rx_pdu[2:]
        # check rx_byte_count: match nb of bits request and check buffer size
        if not ((byte_count >= byte_length(bit_nb)) and
                (byte_count == len(pdu_coils_part))):
            self._last_error = MB_RECV_ERR
            self._debug_msg('read_coils(): rx byte count mismatch')
            self.close()
            return None
        # allocate coils list to return
        ret_coils = [False] * bit_nb
        # populate it with coils value from the rx pdu
        for i in range(bit_nb):
            ret_coils[i] = bool((pdu_coils_part[i // 8] >> i % 8) & 0x01)
        # return read coils
        return ret_coils

    def read_discrete_inputs(self, bit_addr, bit_nb=1):
        """Modbus function READ_DISCRETE_INPUTS (0x02).

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_nb: number of bits to read (1 to 2000)
        :type bit_nb: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        if not 0 <= int(bit_addr) <= 0xffff:
            raise ValueError('bit_addr out of range (valid from 0 to 65535)')
        if not 1 <= int(bit_nb) <= 2000:
            raise ValueError('bit_nb out of range (valid from 1 to 2000)')
        if int(bit_addr) + int(bit_nb) > 0x10000:
            raise ValueError('read after end of modbus address space')
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', READ_DISCRETE_INPUTS, bit_addr, bit_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=3)
        # check error
        if not rx_pdu:
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with bits value -> bits[] list
        f_bits = rx_pdu[2:]
        # check rx_byte_count: match nb of bits request and check buffer size
        if not ((byte_count >= byte_length(bit_nb)) and
                (byte_count == len(f_bits))):
            self._last_error = MB_RECV_ERR
            self._debug_msg('read_discrete_inputs(): rx byte count mismatch')
            self.close()
            return None
        # allocate a bit_nb size list
        bits = [False] * bit_nb
        # fill bits list with bit items
        for i in range(bit_nb):
            bits[i] = bool((f_bits[i // 8] >> i % 8) & 0x01)
        # return bits list
        return bits

    def read_holding_registers(self, reg_addr, reg_nb=1):
        """Modbus function READ_HOLDING_REGISTERS (0x03).

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_nb: number of registers to read (1 to 125)
        :type reg_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        if not 0 <= int(reg_addr) <= 0xffff:
            raise ValueError('reg_addr out of range (valid from 0 to 65535)')
        if not 1 <= int(reg_nb) <= 125:
            raise ValueError('reg_nb out of range (valid from 1 to 125)')
        if int(reg_addr) + int(reg_nb) > 0x10000:
            raise ValueError('read after end of modbus address space')
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', READ_HOLDING_REGISTERS, reg_addr, reg_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=3)
        # check error
        if not rx_pdu:
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with regs value
        f_regs = rx_pdu[2:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if not ((byte_count >= 2 * reg_nb) and
                (byte_count == len(f_regs))):
            self._last_error = MB_RECV_ERR
            self._debug_msg('read_holding_registers(): rx byte count mismatch')
            self.close()
            return None
        # allocate a reg_nb size list
        registers = [0] * reg_nb
        # fill registers list with register items
        for i in range(reg_nb):
            registers[i] = struct.unpack('>H', f_regs[i * 2:i * 2 + 2])[0]
        # return registers list
        return registers

    def read_input_registers(self, reg_addr, reg_nb=1):
        """Modbus function READ_INPUT_REGISTERS (0x04).

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_nb: number of registers to read (1 to 125)
        :type reg_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        if not 0 <= int(reg_addr) <= 0xffff:
            raise ValueError('reg_addr out of range (valid from 0 to 65535)')
        if not 1 <= int(reg_nb) <= 125:
            raise ValueError('reg_nb out of range (valid from 1 to 125)')
        if int(reg_addr) + int(reg_nb) > 0x10000:
            raise ValueError('read after end of modbus address space')
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', READ_INPUT_REGISTERS, reg_addr, reg_nb)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=3)
        # check error
        if not rx_pdu:
            return None
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with regs value
        f_regs = rx_pdu[2:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if not ((byte_count >= 2 * reg_nb) and
                (byte_count == len(f_regs))):
            self._last_error = MB_RECV_ERR
            self._debug_msg('read_input_registers(): rx byte count mismatch')
            self.close()
            return None
        # allocate a reg_nb size list
        registers = [0] * reg_nb
        # fill registers list with register items
        for i in range(reg_nb):
            registers[i] = struct.unpack('>H', f_regs[i * 2:i * 2 + 2])[0]
        # return registers list
        return registers

    def write_single_coil(self, bit_addr, bit_value):
        """Modbus function WRITE_SINGLE_COIL (0x05).

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_value: bit value to write
        :type bit_value: bool
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        if not 0 <= int(bit_addr) <= 0xffff:
            raise ValueError('bit_addr out of range (valid from 0 to 65535)')
        # format "bit value" field for pdu
        bit_value_raw = (0x0000, 0xff00)[bool(bit_value)]
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', WRITE_SINGLE_COIL, bit_addr, bit_value_raw)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=5)
        # check error
        if not rx_pdu:
            return None
        # response decode
        resp_coil_addr, resp_coil_value = struct.unpack('>HH', rx_pdu[1:5])
        # check response fields
        write_ok = (resp_coil_addr == bit_addr) and (resp_coil_value == bit_value_raw)
        return write_ok

    def write_single_register(self, reg_addr, reg_value):
        """Modbus function WRITE_SINGLE_REGISTER (0x06).

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_value: register value to write
        :type reg_value: int
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        if not 0 <= int(reg_addr) <= 0xffff:
            raise ValueError('reg_addr out of range (valid from 0 to 65535)')
        if not 0 <= int(reg_value) <= 0xffff:
            raise ValueError('reg_value out of range (valid from 0 to 65535)')
        # build pdu and send request
        if not self._send_pdu(struct.pack('>BHH', WRITE_SINGLE_REGISTER, reg_addr, reg_value)):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=5)
        # check error
        if not rx_pdu:
            return None
        # response decode
        resp_reg_addr, resp_reg_value = struct.unpack('>HH', rx_pdu[1:5])
        # check response fields
        write_ok = (resp_reg_addr == reg_addr) and (resp_reg_value == reg_value)
        return write_ok

    def write_multiple_coils(self, bits_addr, bits_value):
        """Modbus function WRITE_MULTIPLE_COILS (0x0F).

        :param bits_addr: bits address (0 to 65535)
        :type bits_addr: int
        :param bits_value: bits values to write
        :type bits_value: list
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        if not 0 <= int(bits_addr) <= 0xffff:
            raise ValueError('bit_addr out of range (valid from 0 to 65535)')
        if not 1 <= len(bits_value) <= 1968:
            raise ValueError('number of coils out of range (valid from 1 to 1968)')
        if int(bits_addr) + len(bits_value) > 0x10000:
            raise ValueError('write after end of modbus address space')
        # build pdu coils part
        # allocate a list of bytes
        byte_l = [0] * byte_length(len(bits_value))
        # populate byte list with coils values
        for i, item in enumerate(bits_value):
            if item:
                byte_l[i // 8] = set_bit(byte_l[i // 8], i % 8)
        # format pdu coils part with byte list
        pdu_coils_part = struct.pack('%dB' % len(byte_l), *byte_l)
        # concatenate pdu parts
        tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_COILS, bits_addr, len(bits_value), len(pdu_coils_part))
        tx_pdu += pdu_coils_part
        # send pdu, return None on error
        if not self._send_pdu(tx_pdu):
            return None
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=5)
        # return None on error
        if not rx_pdu:
            return None
        # response decode
        resp_write_addr, resp_write_count = struct.unpack('>HH', rx_pdu[1:5])
        # check response fields
        write_ok = resp_write_addr == bits_addr and resp_write_count == len(bits_value)
        return write_ok

    def write_multiple_registers(self, regs_addr, regs_value):
        """Modbus function WRITE_MULTIPLE_REGISTERS (0x10).

        :param regs_addr: registers address (0 to 65535)
        :type regs_addr: int
        :param regs_value: registers values to write
        :type regs_value: list
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        if not 0 <= int(regs_addr) <= 0xffff:
            raise ValueError('regs_addr out of range (valid from 0 to 65535)')
        if not 1 <= len(regs_value) <= 123:
            raise ValueError('number of registers out of range (valid from 1 to 123)')
        if int(regs_addr) + len(regs_value) > 0x10000:
            raise ValueError('write after end of modbus address space')
        # init pdu registers part
        pdu_regs_part = bytearray()
        # populate it with register values
        for reg in regs_value:
            # check current register value
            if not 0 <= int(reg) <= 0xffff:
                raise ValueError('regs_value list contains out of range values')
            # pack register for build frame
            pdu_regs_part += struct.pack('>H', reg)
        bytes_nb = len(pdu_regs_part)
        # concatenate pdu parts
        tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_REGISTERS, regs_addr, len(regs_value), bytes_nb)
        tx_pdu += pdu_regs_part
        # send pdu, return False on error
        if not self._send_pdu(tx_pdu):
            return False
        # receive pdu
        rx_pdu = self._recv_pdu(min_len=5)
        # check error
        if not rx_pdu:
            return False
        # response decode
        resp_write_addr, resp_write_count = struct.unpack('>HH', rx_pdu[1:5])
        # check response fields
        write_ok = resp_write_addr == regs_addr and resp_write_count == len(regs_value)
        return write_ok

    def _can_read(self):
        """Wait data available for socket read.

        :returns: True if data available or None if timeout or socket error
        :rtype: bool or None
        """
        if self._sock is None:
            return None
        if select.select([self._sock], [], [], self.timeout)[0]:
            return True
        else:
            self._last_error = MB_TIMEOUT_ERR
            self._debug_msg('timeout error')
            self.close()
            return None

    def _send(self, frame):
        """Send frame over current socket.

        :param frame: modbus frame to send (MBAP + PDU)
        :type frame: str (Python2) or class bytes (Python3)
        :returns: True on success
        :rtype: bool
        """
        # check link
        if self._sock is None:
            self._last_error = MB_SOCK_CLOSE_ERR
            self._debug_msg('call _send on close socket')
            return False
        # send
        try:
            self._sock.send(frame)
        except socket.error:
            self._last_error = MB_SEND_ERR
            self._debug_msg('_send error')
            self.close()
            return False
        return True

    def _send_pdu(self, pdu):
        """Convert modbus PDU to frame and send it.

        :param pdu: modbus frame PDU
        :type pdu: str (Python2) or class bytes (Python3)
        :returns: True on success
        :rtype: bool
        """
        # for auto_open mode, check TCP and open on need
        if self.auto_open and not self.is_open:
            self.open()
        # add mbap header to pdu
        tx_frame = self._add_mbap(pdu)
        # send frame with error check
        if not self._send(tx_frame):
            return False
        # debug
        if self.debug:
            self._pretty_dump('Tx', tx_frame)
        return True

    def _recv(self, size):
        """Receive data over current socket.

        :param size: number of bytes to receive
        :type size: int
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
            self._last_error = MB_RECV_ERR
            self._debug_msg('_recv error')
            self.close()
            return None
        return r_buffer

    def _recv_all(self, size):
        """Receive data over current socket, loop until all bytes is received (avoid TCP frag).

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

    def _recv_pdu(self, min_len=2):
        """Receive the modbus PDU (Protocol Data Unit).

        :param min_len: minimal length of the PDU
        :type min_len: int
        :returns: modbus frame PDU or None if error
        :rtype: bytearray or None
        """
        # receive
        # 7 bytes header (mbap)
        rx_mbap = self._recv_all(7)
        # check recv
        if not (rx_mbap and len(rx_mbap) == 7):
            self._last_error = MB_RECV_ERR
            self._debug_msg('recv MBAP error')
            self.close()
            return None
        # enforce type bytearray
        rx_mbap = bytearray(rx_mbap)
        # decode header
        (rx_hd_tr_id, rx_hd_pr_id,
         rx_hd_length, rx_hd_unit_id) = struct.unpack('>HHHB', rx_mbap)
        # check header
        if not ((rx_hd_tr_id == self._transaction_id) and
                (rx_hd_pr_id == 0) and
                (rx_hd_length < 256) and
                (rx_hd_unit_id == self.unit_id)):
            self._last_error = MB_RECV_ERR
            self._debug_msg('MBAP format error')
            if self.debug:
                self._pretty_dump('Rx', rx_mbap)
            self.close()
            return None
        # end of frame
        rx_pdu = self._recv_all(rx_hd_length - 1)
        if not (rx_pdu and
                (len(rx_pdu) == rx_hd_length - 1) and
                (len(rx_pdu) >= min_len)):
            self._last_error = MB_RECV_ERR
            self._debug_msg('_recv frame body error')
            self.close()
            return None
        # enforce type bytearray
        rx_pdu = bytearray(rx_pdu)
        # dump frame
        if self.debug:
            self._pretty_dump('Rx', rx_mbap + rx_pdu)
        # body decode
        rx_fc = struct.unpack('B', rx_pdu[0:1])[0]
        # for auto_close mode, close socket after each request
        if self.auto_close:
            self.close()
        # check except status
        if rx_fc >= 0x80:
            exp_code = struct.unpack('B', rx_pdu[1:2])[0]
            self._last_error = MB_EXCEPT_ERR
            self._last_except = exp_code
            self._debug_msg('except (code %d)' % exp_code)
            return None
        else:
            self._last_except = EXP_NONE
            return rx_pdu

    def _add_mbap(self, pdu):
        """Return full modbus frame with MBAP (modbus application protocol header) append to PDU.

        :param pdu: modbus PDU (protocol data unit)
        :type pdu: str (Python2) or class bytes (Python3)
        :returns: full modbus frame
        :rtype: str (Python2) or class bytes (Python3)
        """
        # build MBAP
        self._transaction_id = random.randint(0, 65535)
        protocol_id = 0
        length = len(pdu) + 1
        mbap = struct.pack('>HHHB', self._transaction_id, protocol_id, length, self.unit_id)
        # full modbus/TCP frame = [MBAP]PDU
        return mbap + pdu

    def _debug_msg(self, msg):
        """Print debug message if debug mode is on.

        :param msg: debug message
        :type msg: str
        """
        if self.debug:
            print(msg)

    @staticmethod
    def _pretty_dump(label, frame):
        """Dump a modbus frame.

        modbus/TCP format: [MBAP] PDU

        :param label: head label
        :type label: str
        :param frame: modbus frame
        :type frame: str (Python2) or class bytes (Python3)
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
