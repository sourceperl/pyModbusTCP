""" pyModbusTCP Client """

from dataclasses import dataclass, field
from .constants import READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS, \
    WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS, WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, \
    WRITE_READ_MULTIPLE_REGISTERS, ENCAPSULATED_INTERFACE_TRANSPORT, MEI_TYPE_READ_DEVICE_ID, \
    MB_ERR_TXT, MB_NO_ERR, MB_SEND_ERR, MB_RECV_ERR, MB_TIMEOUT_ERR, MB_EXCEPT_ERR, MB_CONNECT_ERR, \
    EXP_TXT, EXP_DETAILS, EXP_NONE, MB_SOCK_CLOSE_ERR, VERSION
from .utils import byte_length, set_bit, valid_host
import random
import socket
from socket import AF_UNSPEC, SOCK_STREAM
import struct
from typing import Dict


@dataclass
class DeviceIdentificationResponse:
    """Modbus TCP client function read_device_identification() response struct.
    
    :param conformity_level: this represents supported access and object type
    :type conformity_level: int
    :param more_follows: for stream request can be set to 0xff if other objects are available (0x00 in other cases)
    :type more_follows: int
    :param next_object_id: the next object id to be asked by following transaction
    :type next_object_id: int
    :param objects_by_id: a dictionary with requested object (dict keys are object id as int)
    :type objects_by_id: dict
    """
    conformity_level: int = 0
    more_follows: int = 0
    next_object_id: int = 0
    objects_by_id: Dict[int, bytes] = field(default_factory=lambda: {})

    @property
    def vendor_name(self):
        return self.objects_by_id.get(0x00)

    @property
    def product_code(self):
        return self.objects_by_id.get(0x01)

    @property
    def major_minor_revision(self):
        return self.objects_by_id.get(0x02)

    @property
    def vendor_url(self):
        return self.objects_by_id.get(0x03)

    @property
    def product_name(self):
        return self.objects_by_id.get(0x04)

    @property
    def model_name(self):
        return self.objects_by_id.get(0x05)

    @property
    def user_application_name(self):
        return self.objects_by_id.get(0x06)


class ModbusClient:
    """Modbus TCP client."""

    class _InternalError(Exception):
        pass

    class _NetworkError(_InternalError):
        def __init__(self, code, message):
            self.code = code
            self.message = message

    class _ModbusExcept(_InternalError):
        def __init__(self, code):
            self.code = code

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
        self._sock = socket.socket()
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

    def __repr__(self):
        r_str = 'ModbusClient(host=\'%s\', port=%d, unit_id=%d, timeout=%.2f, debug=%s, auto_open=%s, auto_close=%s)'
        r_str %= (self.host, self.port, self.unit_id, self.timeout, self.debug, self.auto_open, self.auto_close)
        return r_str

    def __del__(self):
        self.close()

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
        Setting timeout to a new value will close the current socket.
        """
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        # enforce type
        value = float(value)
        # check validity
        if 0 < value < 3600:
            if self._timeout != value:
                self.close()
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
        return self._sock.fileno() > 0

    def open(self):
        """Connect to modbus server (open TCP connection).

        :returns: connect status (True on success)
        :rtype: bool
        """
        try:
            self._open()
            return True
        except ModbusClient._NetworkError as e:
            self._req_except_handler(e)
            return False

    def _open(self):
        """Connect to modbus server (open TCP connection)."""
        # open an already open socket -> reset it
        if self.is_open:
            self.close()
        # init socket and connect
        # list available sockets on the target host/port
        # AF_xxx : AF_INET -> IPv4, AF_INET6 -> IPv6,
        #          AF_UNSPEC -> IPv6 (priority on some system) or 4
        # list available socket on target host
        for res in socket.getaddrinfo(self.host, self.port, AF_UNSPEC, SOCK_STREAM):
            af, sock_type, proto, canon_name, sa = res
            try:
                self._sock = socket.socket(af, sock_type, proto)
            except socket.error:
                continue
            try:
                self._sock.settimeout(self.timeout)
                self._sock.connect(sa)
            except socket.error:
                self._sock.close()
                continue
            break
        # check connect status
        if not self.is_open:
            raise ModbusClient._NetworkError(MB_CONNECT_ERR, 'connection refused')

    def close(self):
        """Close current TCP connection."""
        self._sock.close()

    def custom_request(self, pdu):
        """Send a custom modbus request.

        :param pdu: a modbus PDU (protocol data unit)
        :type pdu: bytes
        :returns: modbus frame PDU or None if error
        :rtype: bytes or None
        """
        # make request
        try:
            return self._req_pdu(pdu)
        # handle errors during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

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
        # make request
        try:
            tx_pdu = struct.pack('>BHH', READ_COILS, bit_addr, bit_nb)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            # field "byte count" from PDU
            byte_count = rx_pdu[1]
            # coils PDU part
            rx_pdu_coils = rx_pdu[2:]
            # check rx_byte_count: match nb of bits request and check buffer size
            if byte_count < byte_length(bit_nb) or byte_count != len(rx_pdu_coils):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
            # allocate coils list to return
            ret_coils = [False] * bit_nb
            # populate it with coils value from the rx PDU
            for i in range(bit_nb):
                ret_coils[i] = bool((rx_pdu_coils[i // 8] >> i % 8) & 0x01)
            # return read coils
            return ret_coils
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

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
        # make request
        try:
            tx_pdu = struct.pack('>BHH', READ_DISCRETE_INPUTS, bit_addr, bit_nb)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            # extract field "byte count"
            byte_count = rx_pdu[1]
            # frame with bits value -> bits[] list
            rx_pdu_d_inputs = rx_pdu[2:]
            # check rx_byte_count: match nb of bits request and check buffer size
            if byte_count < byte_length(bit_nb) or byte_count != len(rx_pdu_d_inputs):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
            # allocate a bit_nb size list
            bits = [False] * bit_nb
            # fill bits list with bit items
            for i in range(bit_nb):
                bits[i] = bool((rx_pdu_d_inputs[i // 8] >> i % 8) & 0x01)
            # return bits list
            return bits
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

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
        # make request
        try:
            tx_pdu = struct.pack('>BHH', READ_HOLDING_REGISTERS, reg_addr, reg_nb)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            # extract field "byte count"
            byte_count = rx_pdu[1]
            # frame with regs value
            f_regs = rx_pdu[2:]
            # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
            if byte_count < 2 * reg_nb or byte_count != len(f_regs):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
            # allocate a reg_nb size list
            registers = [0] * reg_nb
            # fill registers list with register items
            for i in range(reg_nb):
                registers[i] = struct.unpack('>H', f_regs[i * 2:i * 2 + 2])[0]
            # return registers list
            return registers
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

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
        # make request
        try:
            tx_pdu = struct.pack('>BHH', READ_INPUT_REGISTERS, reg_addr, reg_nb)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            # extract field "byte count"
            byte_count = rx_pdu[1]
            # frame with regs value
            f_regs = rx_pdu[2:]
            # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
            if byte_count < 2 * reg_nb or byte_count != len(f_regs):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
            # allocate a reg_nb size list
            registers = [0] * reg_nb
            # fill registers list with register items
            for i in range(reg_nb):
                registers[i] = struct.unpack('>H', f_regs[i * 2:i * 2 + 2])[0]
            # return registers list
            return registers
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

    def read_device_identification(self, read_code=1, object_id=0):
        """Modbus function Read Device Identification (0x2B/0x0E).

        :param read_code: read device id code, 1 to 3 for respectively: basic, regular and extended stream access,
            4 for one specific identification object individual access (default is 1)
        :type read_code: int
        :param object_id: object id of the first object to obtain (default is 0)
        :type object_id: int
        :returns: a DeviceIdentificationResponse instance with the data or None if the requests fails
        :rtype: DeviceIdentificationResponse or None
        """
        # check params
        if not 1 <= int(read_code) <= 4:
            raise ValueError('read_code out of range (valid from 1 to 4)')
        if not 0 <= int(object_id) <= 0xff:
            raise ValueError('object_id out of range (valid from 0 to 255)')
        # make request
        try:
            tx_pdu = struct.pack('BBBB', ENCAPSULATED_INTERFACE_TRANSPORT, MEI_TYPE_READ_DEVICE_ID, read_code, object_id)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=7)
            # init response object for populate it
            response = DeviceIdentificationResponse()
            # extract fields
            # field "conformity level"
            response.conformity_level = rx_pdu[3]
            # field "more follows"
            response.more_follows = rx_pdu[4]
            # field "next object id"
            response.next_object_id = rx_pdu[5]
            # field "number of objects"
            nb_of_objs = rx_pdu[6]
            # decode [[obj_id, obj_len, obj_value],...]
            pdu_offset = 7
            for _ in range(nb_of_objs):
                # parse object PDU bytes
                try:
                    obj_id = rx_pdu[pdu_offset]
                    obj_len = rx_pdu[pdu_offset+1]
                    obj_value = rx_pdu[pdu_offset+2:pdu_offset+2+obj_len]
                except IndexError:
                    raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
                if obj_len != len(obj_value):
                    raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
                # set offset to next object
                pdu_offset += 2 + obj_len
                # add result to request list
                response.objects_by_id[obj_id] = obj_value
            return response
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

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
        # make request
        try:
            # format "bit value" field for PDU
            bit_value_raw = (0x0000, 0xff00)[bool(bit_value)]
            # make a request
            tx_pdu = struct.pack('>BHH', WRITE_SINGLE_COIL, bit_addr, bit_value_raw)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=5)
            # decode reply
            resp_coil_addr, resp_coil_value = struct.unpack('>HH', rx_pdu[1:5])
            # check server reply
            if (resp_coil_addr != bit_addr) or (resp_coil_value != bit_value_raw):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'server reply does not match the request')
            return True
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return False

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
        # make request
        try:
            # make a request
            tx_pdu = struct.pack('>BHH', WRITE_SINGLE_REGISTER, reg_addr, reg_value)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=5)
            # decode reply
            resp_reg_addr, resp_reg_value = struct.unpack('>HH', rx_pdu[1:5])
            # check server reply
            if (resp_reg_addr != reg_addr) or (resp_reg_value != reg_value):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'server reply does not match the request')
            return True
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return False

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
        # make request
        try:
            # build PDU coils part
            # allocate a list of bytes
            byte_l = [0] * byte_length(len(bits_value))
            # populate byte list with coils values
            for i, item in enumerate(bits_value):
                if item:
                    byte_l[i // 8] = set_bit(byte_l[i // 8], i % 8)
            # format PDU coils part with byte list
            pdu_coils_part = struct.pack('%dB' % len(byte_l), *byte_l)
            # concatenate PDU parts
            tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_COILS, bits_addr, len(bits_value), len(pdu_coils_part))
            tx_pdu += pdu_coils_part
            # make a request
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=5)
            # response decode
            resp_write_addr, resp_write_count = struct.unpack('>HH', rx_pdu[1:5])
            # check response fields
            write_ok = resp_write_addr == bits_addr and resp_write_count == len(bits_value)
            return write_ok
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return False

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
        # make request
        try:
            # init PDU registers part
            pdu_regs_part = b''
            # populate it with register values
            for reg in regs_value:
                # check current register value
                if not 0 <= int(reg) <= 0xffff:
                    raise ValueError('regs_value list contains out of range values')
                # pack register for build frame
                pdu_regs_part += struct.pack('>H', reg)
            bytes_nb = len(pdu_regs_part)
            # concatenate PDU parts
            tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_REGISTERS, regs_addr, len(regs_value), bytes_nb)
            tx_pdu += pdu_regs_part
            # make a request
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=5)
            # response decode
            resp_write_addr, resp_write_count = struct.unpack('>HH', rx_pdu[1:5])
            # check response fields
            write_ok = resp_write_addr == regs_addr and resp_write_count == len(regs_value)
            return write_ok
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return False

    def write_read_multiple_registers(self, write_addr, write_values, read_addr, read_nb=1):
        """Modbus function WRITE_READ_MULTIPLE_REGISTERS (0x17).

        :param write_addr: write registers address (0 to 65535)
        :type write_addr: int
        :param write_values: registers values to write
        :type write_values: list
        :param read_addr: read register address (0 to 65535)
        :type read_addr: int
        :param read_nb: number of registers to read (1 to 125)
        :type read_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        check_l = [(not 0 <= int(write_addr) <= 0xffff, 'write_addr out of range (valid from 0 to 65535)'),
                   (not 1 <= len(write_values) <= 121, 'number of registers out of range (valid from 1 to 121)'),
                   (int(write_addr) + len(write_values) > 0x10000, 'write after end of modbus address space'),
                   (not 0 <= int(read_addr) <= 0xffff, 'read_addr out of range (valid from 0 to 65535)'),
                   (not 1 <= int(read_nb) <= 125, 'read_nb out of range (valid from 1 to 125)'),
                   (int(read_addr) + int(read_nb) > 0x10000, 'read after end of modbus address space'), ]
        for err, msg in check_l:
            if err:
                raise ValueError(msg)
        # make request
        try:
            # init PDU registers part
            pdu_regs_part = b''
            # populate it with register values
            for reg in write_values:
                # check current register value
                if not 0 <= int(reg) <= 0xffff:
                    raise ValueError('write_values list contains out of range values')
                # pack register for build frame
                pdu_regs_part += struct.pack('>H', reg)
            bytes_nb = len(pdu_regs_part)
            # concatenate PDU parts
            tx_pdu = struct.pack('>BHHHHB', WRITE_READ_MULTIPLE_REGISTERS, read_addr, read_nb,
                                 write_addr, len(write_values), bytes_nb)
            tx_pdu += pdu_regs_part
            # make a request
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=4)
            # response decode
            # extract field "byte count"
            byte_count = rx_pdu[1]
            # frame with regs value
            f_regs = rx_pdu[2:]
            # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
            if byte_count < 2 * read_nb or byte_count != len(f_regs):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
            # allocate a reg_nb size list
            registers = [0] * read_nb
            # fill registers list with register items
            for i in range(read_nb):
                registers[i] = struct.unpack('>H', f_regs[i * 2:i * 2 + 2])[0]
            # return registers list
            return registers
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return

    def _send(self, frame):
        """Send frame over current socket.

        :param frame: modbus frame to send (MBAP + PDU)
        :type frame: bytes
        """
        # check socket
        if not self.is_open:
            raise ModbusClient._NetworkError(MB_SOCK_CLOSE_ERR, 'try to send on a close socket')
        # send
        try:
            self._sock.send(frame)
        except socket.timeout:
            self._sock.close()
            raise ModbusClient._NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except socket.error:
            self._sock.close()
            raise ModbusClient._NetworkError(MB_SEND_ERR, 'send error')

    def _send_pdu(self, pdu):
        """Convert modbus PDU to frame and send it.

        :param pdu: modbus frame PDU
        :type pdu: bytes
        """
        # for auto_open mode, check TCP and open on need
        if self.auto_open and not self.is_open:
            self._open()
        # add MBAP header to PDU
        tx_frame = self._add_mbap(pdu)
        # send frame with error check
        self._send(tx_frame)
        # debug
        self._debug_dump('Tx', tx_frame)

    def _recv(self, size):
        """Receive data over current socket.

        :param size: number of bytes to receive
        :type size: int
        :returns: receive data or None if error
        :rtype: bytes
        """
        try:
            r_buffer = self._sock.recv(size)
        except socket.timeout:
            self._sock.close()
            raise ModbusClient._NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except socket.error:
            r_buffer = b''
        # handle recv error
        if not r_buffer:
            self._sock.close()
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'recv error')
        return r_buffer

    def _recv_all(self, size):
        """Receive data over current socket, loop until all bytes is received (avoid TCP frag).

        :param size: number of bytes to receive
        :type size: int
        :returns: receive data or None if error
        :rtype: bytes
        """
        r_buffer = b''
        while len(r_buffer) < size:
            r_buffer += self._recv(size - len(r_buffer))
        return r_buffer

    def _recv_pdu(self, min_len=2):
        """Receive the modbus PDU (Protocol Data Unit).

        :param min_len: minimal length of the PDU
        :type min_len: int
        :returns: modbus frame PDU or None if error
        :rtype: bytes or None
        """
        # receive 7 bytes header (MBAP)
        rx_mbap = self._recv_all(7)
        # decode MBAP
        (f_transaction_id, f_protocol_id, f_length, f_unit_id) = struct.unpack('>HHHB', rx_mbap)
        # check MBAP fields
        f_transaction_err = f_transaction_id != self._transaction_id
        f_protocol_err = f_protocol_id != 0
        f_length_err = f_length >= 256
        f_unit_id_err = f_unit_id != self.unit_id
        # checking error status of fields
        if f_transaction_err or f_protocol_err or f_length_err or f_unit_id_err:
            self.close()
            self._debug_dump('Rx', rx_mbap)
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'MBAP checking error')
        # recv PDU
        rx_pdu = self._recv_all(f_length - 1)
        # for auto_close mode, close socket after each request
        if self.auto_close:
            self.close()
        # dump frame
        self._debug_dump('Rx', rx_mbap + rx_pdu)
        # body decode
        # check PDU length for global minimal frame (an except frame: func code + exp code)
        if len(rx_pdu) < 2:
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'PDU length is too short')
        # extract function code
        rx_fc = rx_pdu[0]
        # check except status
        if rx_fc >= 0x80:
            exp_code = rx_pdu[1]
            raise ModbusClient._ModbusExcept(exp_code)
        # check PDU length for specific request set in min_len (keep this after except checking)
        if len(rx_pdu) < min_len:
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'PDU length is too short for current request')
        # if no error, return PDU
        return rx_pdu

    def _add_mbap(self, pdu):
        """Return full modbus frame with MBAP (modbus application protocol header) append to PDU.

        :param pdu: modbus PDU (protocol data unit)
        :type pdu: bytes
        :returns: full modbus frame
        :rtype: bytes
        """
        # build MBAP
        self._transaction_id = random.randint(0, 65535)
        protocol_id = 0
        length = len(pdu) + 1
        mbap = struct.pack('>HHHB', self._transaction_id, protocol_id, length, self.unit_id)
        # full modbus/TCP frame = [MBAP]PDU
        return mbap + pdu

    def _req_pdu(self, tx_pdu, rx_min_len=2):
        """Request processing (send and recv PDU).

        :param tx_pdu: modbus PDU (protocol data unit) to send
        :type tx_pdu: bytes
        :param rx_min_len: min length of receive PDU
        :type rx_min_len: int
        :returns: the receive PDU or None if error
        :rtype: bytes
        """
        # init request engine
        self._req_init()
        # send PDU
        self._send_pdu(tx_pdu)
        # return receive PDU
        return self._recv_pdu(min_len=rx_min_len)

    def _req_init(self):
        """Reset request status flags."""
        self._last_error = MB_NO_ERR
        self._last_except = EXP_NONE

    def _req_except_handler(self, _except):
        """Global handler for internal exceptions."""
        # on request network error
        if isinstance(_except, ModbusClient._NetworkError):
            self._last_error = _except.code
            self._debug_msg(_except.message)
        # on request modbus except
        if isinstance(_except, ModbusClient._ModbusExcept):
            self._last_error = MB_EXCEPT_ERR
            self._last_except = _except.code
            self._debug_msg('modbus exception (code %d "%s")' % (self.last_except, self.last_except_as_txt))

    def _debug_msg(self, msg):
        """Print debug message if debug mode is on.

        :param msg: debug message
        :type msg: str
        """
        if self.debug:
            print(msg)

    def _debug_dump(self, label, frame):
        """Print debug dump if debug mode is on.

        :param label: head label
        :type label: str
        :param frame: modbus frame
        :type frame: bytes
        """
        if self.debug:
            self._pretty_dump(label, frame)

    @staticmethod
    def _pretty_dump(label, frame):
        """Dump a modbus frame.

        modbus/TCP format: [MBAP] PDU

        :param label: head label
        :type label: str
        :param frame: modbus frame
        :type frame: bytes
        """
        # split data string items to a list of hex value
        dump = ['%02X' % c for c in frame]
        # format message
        dump_mbap = ' '.join(dump[0:7])
        dump_pdu = ' '.join(dump[7:])
        msg = '[%s] %s' % (dump_mbap, dump_pdu)
        # print result
        print(label)
        print(msg)
