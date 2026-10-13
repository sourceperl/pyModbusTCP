""" pyModbusTCP Client """

import logging
import math
import random
import socket
import struct
import time
from binascii import hexlify
from dataclasses import dataclass, field
from socket import AF_UNSPEC, IPPROTO_TCP, SOCK_STREAM, TCP_NODELAY
from types import TracebackType
from typing import Dict, Iterable, List, Optional, Type, Union

from .constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    EXP_DETAILS,
    EXP_NONE,
    EXP_TXT,
    MAX_PDU_SIZE,
    MB_CONNECT_ERR,
    MB_ERR_TXT,
    MB_EXCEPT_ERR,
    MB_NO_ERR,
    MB_RECV_ERR,
    MB_SEND_ERR,
    MB_SOCK_CLOSE_ERR,
    MB_TIMEOUT_ERR,
    MBAP_MAX_LENGTH,
    MBAP_MIN_LENGTH,
    MBAP_SIZE,
    MEI_TYPE_READ_DEVICE_ID,
    READ_COILS,
    READ_DISCRETE_INPUTS,
    READ_HOLDING_REGISTERS,
    READ_INPUT_REGISTERS,
    VERSION,
    WRITE_MULTIPLE_COILS,
    WRITE_MULTIPLE_REGISTERS,
    WRITE_READ_MULTIPLE_REGISTERS,
    WRITE_SINGLE_COIL,
    WRITE_SINGLE_REGISTER,
)
from .utils import (
    _pack_bits,
    _to_bool,
    _to_bool_list,
    _to_int,
    _to_int_list,
    _unpack_bits,
    byte_length,
    valid_host,
)

# add a logger for pyModbusTCP.client
logger = logging.getLogger(__name__)

# modbus limits (number of items by request)
_MAX_READ_BITS = 2000
_MAX_WRITE_BITS = 1968
_MAX_READ_REGS = 125
_MAX_WRITE_REGS = 123
_MAX_WRITE_READ_REGS = 121


def _decode_regs(data: bytes, reg_nb: int) -> List[int]:
    """Decode the first reg_nb big-endian 16 bits registers of a modbus registers field."""
    return list(struct.unpack('>%dH' % reg_nb, data[:2 * reg_nb]))


def _check_addr_nb(addr: int, nb: int, max_nb: int, addr_name: str, nb_name: str, action: str = 'read') -> None:
    """Check a modbus address and a number of items (both already converted to int).

    :raises ValueError: if address, number of items or the end of the area is out of range
    """
    if not 0 <= addr <= 0xffff:
        raise ValueError('%s out of range (valid from 0 to 65535)' % addr_name)
    if not 1 <= nb <= max_nb:
        raise ValueError('%s out of range (valid from 1 to %d)' % (nb_name, max_nb))
    if addr + nb > 0x10000:
        raise ValueError('%s after end of modbus address space' % action)


def _check_regs_values(values: List[int], name: str) -> None:
    """Check that every register value fit in 16 bits (values is a non-empty list of int).

    :raises ValueError: if a value is out of the 0 to 65535 range
    """
    if min(values) < 0 or max(values) > 0xffff:
        raise ValueError('%s list contains out of range values' % name)


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
    objects_by_id: Dict[int, bytes] = field(default_factory=dict)

    @property
    def vendor_name(self) -> Optional[bytes]:
        return self.objects_by_id.get(0x00)

    @property
    def product_code(self) -> Optional[bytes]:
        return self.objects_by_id.get(0x01)

    @property
    def major_minor_revision(self) -> Optional[bytes]:
        return self.objects_by_id.get(0x02)

    @property
    def vendor_url(self) -> Optional[bytes]:
        return self.objects_by_id.get(0x03)

    @property
    def product_name(self) -> Optional[bytes]:
        return self.objects_by_id.get(0x04)

    @property
    def model_name(self) -> Optional[bytes]:
        return self.objects_by_id.get(0x05)

    @property
    def user_application_name(self) -> Optional[bytes]:
        return self.objects_by_id.get(0x06)


class ModbusClient:
    """Modbus TCP client.

    An instance is not thread-safe: share it between threads only with an external lock.
    """

    class _InternalError(Exception):
        pass

    class _NetworkError(_InternalError):
        def __init__(self, code: int, message: str):
            super().__init__(message)
            self.code = code
            self.message = message

    class _ModbusExcept(_InternalError):
        def __init__(self, code: int):
            super().__init__('modbus exception 0x%02X' % code)
            self.code = code

    def __init__(self, host: str = 'localhost', port: int = 502, unit_id: int = 1, timeout: float = 30.0,
                 auto_open: bool = True, auto_close: bool = False, no_delay: bool = True):
        """Constructor.

        :param host: hostname or IPv4/IPv6 address server address
        :type host: str
        :param port: TCP port number
        :type port: int
        :param unit_id: unit ID
        :type unit_id: int
        :param timeout: timeout in seconds for a request (send + receive), not for each socket operation
        :type timeout: float
        :param auto_open: auto TCP connect
        :type auto_open: bool
        :param auto_close: auto TCP close
        :type auto_close: bool
        :param no_delay: disable Nagle's algorithm on TCP connection (True by default)
        :type no_delay: bool
        :return: Object ModbusClient
        :rtype: ModbusClient
        """
        # private
        # set _sock first: __del__() always needs it, even if a validation below fails
        self._sock: Optional[socket.socket] = None
        # constructor arguments: validate them
        self._host = self._validate_host(host)
        self._port = self._validate_port(port)
        self._unit_id = self._validate_unit_id(unit_id)
        self._timeout = self._validate_timeout(timeout)
        self._auto_open = _to_bool(auto_open, 'auto_open')
        self._auto_close = _to_bool(auto_close, 'auto_close')
        self._no_delay = _to_bool(no_delay, 'no_delay')
        # internal variables
        self._deadline = 0.0  # end of the current request (time.monotonic() value), see _send() and _recv()
        # MBAP transaction ID: a counter incremented at each request
        self._transaction_id = random.randint(0, 0xffff)
        self._version = VERSION  # this package version number
        self._last_error = MB_NO_ERR  # last error code
        self._last_except = EXP_NONE  # last except code

    def __repr__(self) -> str:
        r_str = 'ModbusClient(host=\'%s\', port=%d, unit_id=%d, timeout=%.2f, auto_open=%s, auto_close=%s, no_delay=%s)'
        r_str %= (self.host, self.port, self.unit_id, self.timeout, self.auto_open, self.auto_close, self.no_delay)
        return r_str

    def __del__(self) -> None:
        # don't call close() if __init__() failed before _sock is set
        if hasattr(self, '_sock'):
            self.close()

    def __enter__(self) -> 'ModbusClient':
        """Context manager: the connection is closed on exit (it is opened at first request with auto_open)."""
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_value: Optional[BaseException],
                 traceback: Optional[TracebackType]) -> None:
        self.close()

    @property
    def host(self) -> str:
        """Get or set the server to connect to.

        This can be any string with a valid IPv4 / IPv6 address or hostname.
        Setting host to a new value will close the current socket.
        """
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        # validate
        value = self._validate_host(value)
        value_change = self._host != value
        self._host = value
        # on change handler
        if value_change:
            self._on_change_host()

    def _validate_host(self, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError('host must be a str')
        if not valid_host(value):
            raise ValueError('host can\'t be set (not a valid IP address or hostname)')
        return value

    def _on_change_host(self) -> None:
        self.close()

    @property
    def port(self) -> int:
        """Get or set the current TCP port (default is 502).

        Setting port to a new value will close the current socket.
        """
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        # validate
        value = self._validate_port(value)
        value_change = value != self._port
        self._port = value
        # on change handler
        if value_change:
            self._on_change_port()

    def _validate_port(self, value: object) -> int:
        # bool is an int subclass: port=True would silently be port 1
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError('port must be an int')
        if not 0 < value < 65536:
            raise ValueError('port can\'t be set (valid if 0 < port < 65536)')
        return value

    def _on_change_port(self) -> None:
        self.close()

    @property
    def unit_id(self) -> int:
        """Get or set the modbus unit identifier (default is 1).

        Any int from 0 to 255 is valid.
        """
        return self._unit_id

    @unit_id.setter
    def unit_id(self, value: int) -> None:
        # validate
        value = self._validate_unit_id(value)
        value_change = value != self._unit_id
        self._unit_id = value
        # on change handler
        if value_change:
            self._on_change_unit_id()

    def _validate_unit_id(self, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError('unit_id must be an int')
        if not 0 <= value <= 255:
            raise ValueError('unit_id can\'t be set (valid from 0 to 255)')
        return value

    def _on_change_unit_id(self) -> None:
        pass

    @property
    def timeout(self) -> float:
        """Get or set requests timeout (default is 30 seconds).

        The argument may be a floating point number for sub-second precision.
        This is a limit for the whole request (send + receive), applied from the next request on
        (the current connection is kept).
        """
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        # validate
        value = self._validate_timeout(value)
        value_change = value != self._timeout
        self._timeout = value
        # on change handler
        if value_change:
            self._on_change_timeout()

    def _validate_timeout(self, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise TypeError('timeout must be a float')
        if not (math.isfinite(value) and 0.0 < value < 3600.0):
            raise ValueError('timeout can\'t be set (valid between 0 and 3600)')
        return float(value)

    def _on_change_timeout(self) -> None:
        pass

    @property
    def auto_open(self) -> bool:
        """Get or set automatic TCP connect mode (True = turn on)."""
        return self._auto_open

    @auto_open.setter
    def auto_open(self, value: bool) -> None:
        self._auto_open = _to_bool(value, 'auto_open')

    @property
    def auto_close(self) -> bool:
        """Get or set automatic TCP close after each request mode (True = turn on)."""
        return self._auto_close

    @auto_close.setter
    def auto_close(self, value: bool) -> None:
        self._auto_close = _to_bool(value, 'auto_close')

    @property
    def no_delay(self) -> bool:
        """Get or set TCP_NODELAY mode on connection (True = disable Nagle's algorithm, default)."""
        return self._no_delay

    @no_delay.setter
    def no_delay(self, value: bool) -> None:
        self._no_delay = _to_bool(value, 'no_delay')
        # apply it at once on an open connection (like for the other settings, no need to reconnect)
        if self.is_open:
            self._set_sock_tcp_no_delay(value=self.no_delay)

    @property
    def is_open(self) -> bool:
        """Get current status of the TCP connection (True = open)."""
        if self._sock is None:
            return False
        try:
            return self._sock.fileno() >= 0
        except (AttributeError, OSError):
            return False

    def open(self) -> bool:
        """Connect to modbus server (open TCP connection).

        :returns: connect status (True on success)
        :rtype: bool
        """
        self._req_init()
        try:
            self._open()
            return True
        except ModbusClient._NetworkError as e:
            self._req_except_handler(e)
            return False

    @property
    def version(self) -> str:
        """Return the current package version as a str."""
        return self._version

    @property
    def last_error(self) -> int:
        """Last error code."""
        return self._last_error

    @property
    def last_error_as_txt(self) -> str:
        """Human-readable text that describe last error."""
        return MB_ERR_TXT.get(self._last_error, 'unknown error')

    @property
    def last_except(self) -> int:
        """Return the last modbus exception code."""
        return self._last_except

    @property
    def last_except_as_txt(self) -> str:
        """Short human-readable text that describe last modbus exception."""
        default_str = 'unreferenced exception 0x%X' % self._last_except
        return EXP_TXT.get(self._last_except, default_str)

    @property
    def last_except_as_full_txt(self) -> str:
        """Verbose human-readable text that describe last modbus exception."""
        default_str = 'unreferenced exception 0x%X' % self._last_except
        return EXP_DETAILS.get(self._last_except, default_str)

    def _open(self) -> None:
        """Connect to modbus server (open TCP connection)."""
        # open an already open socket -> reset it
        if self.is_open:
            self.close()
        # list available sockets on the target host/port
        # AF_xxx : AF_INET -> IPv4, AF_INET6 -> IPv6,
        #          AF_UNSPEC -> IPv6 (priority on some system) or 4
        try:
            addr_infos = socket.getaddrinfo(self.host, self.port, AF_UNSPEC, SOCK_STREAM)
        except socket.gaierror as e:
            # name resolution failure: report it like any other connect error (no exception for the caller)
            raise ModbusClient._NetworkError(MB_CONNECT_ERR, 'host name resolution error: %s' % e)
        # try each address in turn, keep the last failure reason for the error message
        last_err: Optional[OSError] = None
        for af, sock_type, proto, _canon_name, sa in addr_infos:
            try:
                self._sock = socket.socket(af, sock_type, proto)
            except OSError as e:
                last_err = e
                continue
            try:
                self._sock.settimeout(self.timeout)
                # disable Nagle's algorithm: modbus requests are small frames
                # that expect an immediate reply (request/response pattern)
                self._set_sock_tcp_no_delay(self._no_delay)
                self._sock.connect(sa)
            except OSError as e:
                last_err = e
                self.close()
                continue
            break
        # check connect status
        if not self.is_open:
            reason = 'connection refused' if last_err is None else 'connect error: %s' % last_err
            raise ModbusClient._NetworkError(MB_CONNECT_ERR, reason)

    def close(self) -> None:
        """Close current TCP connection."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None

    def custom_request(self, pdu: Union[bytes, bytearray]) -> Optional[bytes]:
        """Send a custom modbus request.

        The reply must have the same function code (or the same with the exception flag): otherwise it is an error.

        :param pdu: a modbus PDU (protocol data unit) of 1 to 253 bytes: function code followed by the request data
            (the function code is not checked, a code with the exception flag set (0x80 to 0xFF) is sent as is,
            but its reply can't be accepted: it's always a function code mismatch error)
        :type pdu: bytes or bytearray
        :returns: modbus frame PDU or None if error
        :rtype: bytes or None
        """
        # check params
        if not isinstance(pdu, (bytes, bytearray)):
            raise TypeError('pdu must be a bytes-like object')
        pdu = bytes(pdu)
        if not 1 <= len(pdu) <= MAX_PDU_SIZE:
            raise ValueError('pdu length out of range (valid from 1 to %d bytes)' % MAX_PDU_SIZE)
        # make request
        try:
            return self._req_pdu(pdu)
        # handle errors during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

    def _read_bits_req(self, func_code: int, bit_addr: int, bit_nb: int) -> Optional[List[bool]]:
        """Common part of READ_COILS and READ_DISCRETE_INPUTS (params are already checked)."""
        try:
            tx_pdu = struct.pack('>BHH', func_code, bit_addr, bit_nb)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            # field "byte count" from PDU
            byte_count = rx_pdu[1]
            # bits PDU part
            rx_pdu_bits = rx_pdu[2:]
            # check rx_byte_count: match nb of bits request and check buffer size
            if byte_count < byte_length(bit_nb) or byte_count != len(rx_pdu_bits):
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
            # return bits list
            return _unpack_bits(rx_pdu_bits, bit_nb)
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

    def _read_regs_req(self, func_code: int, reg_addr: int, reg_nb: int) -> Optional[List[int]]:
        """Common part of READ_HOLDING_REGISTERS and READ_INPUT_REGISTERS (params are already checked)."""
        try:
            tx_pdu = struct.pack('>BHH', func_code, reg_addr, reg_nb)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            return self._decode_regs_reply(rx_pdu, reg_nb)
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

    @staticmethod
    def _decode_regs_reply(rx_pdu: bytes, reg_nb: int) -> List[int]:
        """Decode a [func code, byte count, registers...] reply PDU (functions 0x03, 0x04 and 0x17)."""
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with regs value
        f_regs = rx_pdu[2:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if byte_count < 2 * reg_nb or byte_count != len(f_regs):
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
        # return registers list
        return _decode_regs(f_regs, reg_nb)

    def _write_req(self, tx_pdu: bytes, addr: int, value: int) -> bool:
        """Send a write request and check that the reply echoes the [address, value or quantity] of the request."""
        try:
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=5)
            resp_addr, resp_value = struct.unpack('>HH', rx_pdu[1:5])
            if resp_addr != addr or resp_value != value:
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'server reply does not match the request')
            return True
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return False

    def read_coils(self, bit_addr: int, bit_nb: int = 1) -> Optional[List[bool]]:
        """Modbus function READ_COILS (0x01).

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_nb: number of bits to read (1 to 2000)
        :type bit_nb: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        bit_addr = _to_int(bit_addr, 'bit_addr')
        bit_nb = _to_int(bit_nb, 'bit_nb')
        _check_addr_nb(bit_addr, bit_nb, _MAX_READ_BITS, 'bit_addr', 'bit_nb')
        # make request
        return self._read_bits_req(READ_COILS, bit_addr, bit_nb)

    def read_discrete_inputs(self, bit_addr: int, bit_nb: int = 1) -> Optional[List[bool]]:
        """Modbus function READ_DISCRETE_INPUTS (0x02).

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_nb: number of bits to read (1 to 2000)
        :type bit_nb: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        bit_addr = _to_int(bit_addr, 'bit_addr')
        bit_nb = _to_int(bit_nb, 'bit_nb')
        _check_addr_nb(bit_addr, bit_nb, _MAX_READ_BITS, 'bit_addr', 'bit_nb')
        # make request
        return self._read_bits_req(READ_DISCRETE_INPUTS, bit_addr, bit_nb)

    def read_holding_registers(self, reg_addr: int, reg_nb: int = 1) -> Optional[List[int]]:
        """Modbus function READ_HOLDING_REGISTERS (0x03).

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_nb: number of registers to read (1 to 125)
        :type reg_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        reg_addr = _to_int(reg_addr, 'reg_addr')
        reg_nb = _to_int(reg_nb, 'reg_nb')
        _check_addr_nb(reg_addr, reg_nb, _MAX_READ_REGS, 'reg_addr', 'reg_nb')
        # make request
        return self._read_regs_req(READ_HOLDING_REGISTERS, reg_addr, reg_nb)

    def read_input_registers(self, reg_addr: int, reg_nb: int = 1) -> Optional[List[int]]:
        """Modbus function READ_INPUT_REGISTERS (0x04).

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_nb: number of registers to read (1 to 125)
        :type reg_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        reg_addr = _to_int(reg_addr, 'reg_addr')
        reg_nb = _to_int(reg_nb, 'reg_nb')
        _check_addr_nb(reg_addr, reg_nb, _MAX_READ_REGS, 'reg_addr', 'reg_nb')
        # make request
        return self._read_regs_req(READ_INPUT_REGISTERS, reg_addr, reg_nb)

    def read_device_identification(self, read_code: int = 1,
                                   object_id: int = 0) -> Optional[DeviceIdentificationResponse]:
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
        read_code = _to_int(read_code, 'read_code')
        object_id = _to_int(object_id, 'object_id')
        if not 1 <= read_code <= 4:
            raise ValueError('read_code out of range (valid from 1 to 4)')
        if not 0 <= object_id <= 0xff:
            raise ValueError('object_id out of range (valid from 0 to 255)')
        # make request
        try:
            tx_pdu = struct.pack('BBBB', ENCAPSULATED_INTERFACE_TRANSPORT,
                                 MEI_TYPE_READ_DEVICE_ID, read_code, object_id)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=7)
            # the reply must echo the MEI type and the read device id code of the request
            if rx_pdu[1] != MEI_TYPE_READ_DEVICE_ID or rx_pdu[2] != read_code:
                raise ModbusClient._NetworkError(MB_RECV_ERR, 'server reply does not match the request')
            # init response object for populate it
            response = DeviceIdentificationResponse()
            # extract fields
            response.conformity_level = rx_pdu[3]
            response.more_follows = rx_pdu[4]
            response.next_object_id = rx_pdu[5]
            nb_of_objs = rx_pdu[6]
            # decode [[obj_id, obj_len, obj_value],...]
            pdu_offset = 7
            for _ in range(nb_of_objs):
                # an object header is 2 bytes (id + length), followed by length bytes
                if pdu_offset + 2 > len(rx_pdu):
                    raise ModbusClient._NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
                obj_id = rx_pdu[pdu_offset]
                obj_len = rx_pdu[pdu_offset + 1]
                obj_value = rx_pdu[pdu_offset + 2:pdu_offset + 2 + obj_len]
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

    def write_single_coil(self, bit_addr: int, bit_value: bool) -> bool:
        """Modbus function WRITE_SINGLE_COIL (0x05).

        :param bit_addr: bit address (0 to 65535)
        :type bit_addr: int
        :param bit_value: bit value to write
        :type bit_value: bool
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        bit_addr = _to_int(bit_addr, 'bit_addr')
        bit_value = _to_bool(bit_value, 'bit_value')
        if not 0 <= bit_addr <= 0xffff:
            raise ValueError('bit_addr out of range (valid from 0 to 65535)')
        # format "bit value" field for PDU
        bit_value_raw = 0xff00 if bit_value else 0x0000
        # make request
        tx_pdu = struct.pack('>BHH', WRITE_SINGLE_COIL, bit_addr, bit_value_raw)
        return self._write_req(tx_pdu, bit_addr, bit_value_raw)

    def write_single_register(self, reg_addr: int, reg_value: int) -> bool:
        """Modbus function WRITE_SINGLE_REGISTER (0x06).

        :param reg_addr: register address (0 to 65535)
        :type reg_addr: int
        :param reg_value: register value to write
        :type reg_value: int
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        reg_addr = _to_int(reg_addr, 'reg_addr')
        reg_value = _to_int(reg_value, 'reg_value')
        if not 0 <= reg_addr <= 0xffff:
            raise ValueError('reg_addr out of range (valid from 0 to 65535)')
        if not 0 <= reg_value <= 0xffff:
            raise ValueError('reg_value out of range (valid from 0 to 65535)')
        # make request
        tx_pdu = struct.pack('>BHH', WRITE_SINGLE_REGISTER, reg_addr, reg_value)
        return self._write_req(tx_pdu, reg_addr, reg_value)

    def write_multiple_coils(self, bits_addr: int, bits_value: Iterable[int]) -> bool:
        """Modbus function WRITE_MULTIPLE_COILS (0x0F).

        :param bits_addr: bits address (0 to 65535)
        :type bits_addr: int
        :param bits_value: bits values to write (any iterable of bool or int)
        :type bits_value: list
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        bits_addr = _to_int(bits_addr, 'bits_addr')
        bits_value = _to_bool_list(bits_value, 'bits_value')
        _check_addr_nb(bits_addr, len(bits_value), _MAX_WRITE_BITS, 'bits_addr', 'number of coils', 'write')
        # build PDU: header + coils part (pack all the bits in bytes at once)
        pdu_coils_part = _pack_bits(bits_value)
        tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_COILS, bits_addr, len(bits_value), len(pdu_coils_part))
        tx_pdu += pdu_coils_part
        # make request
        return self._write_req(tx_pdu, bits_addr, len(bits_value))

    def write_multiple_registers(self, regs_addr: int, regs_value: Iterable[int]) -> bool:
        """Modbus function WRITE_MULTIPLE_REGISTERS (0x10).

        :param regs_addr: registers address (0 to 65535)
        :type regs_addr: int
        :param regs_value: registers values to write (any iterable of int)
        :type regs_value: list
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        regs_addr = _to_int(regs_addr, 'regs_addr')
        regs_value = _to_int_list(regs_value, 'regs_value')
        _check_addr_nb(regs_addr, len(regs_value), _MAX_WRITE_REGS, 'regs_addr', 'number of registers', 'write')
        _check_regs_values(regs_value, 'regs_value')
        # build PDU: header + registers part (pack all registers in one call)
        pdu_regs_part = struct.pack('>%dH' % len(regs_value), *regs_value)
        tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_REGISTERS, regs_addr, len(regs_value), len(pdu_regs_part))
        tx_pdu += pdu_regs_part
        # make request
        return self._write_req(tx_pdu, regs_addr, len(regs_value))

    def write_read_multiple_registers(self, write_addr: int, write_values: Iterable[int],
                                      read_addr: int, read_nb: int = 1) -> Optional[List[int]]:
        """Modbus function WRITE_READ_MULTIPLE_REGISTERS (0x17).

        :param write_addr: write registers address (0 to 65535)
        :type write_addr: int
        :param write_values: registers values to write (any iterable of int)
        :type write_values: list
        :param read_addr: read register address (0 to 65535)
        :type read_addr: int
        :param read_nb: number of registers to read (1 to 125)
        :type read_nb: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        write_addr = _to_int(write_addr, 'write_addr')
        write_values = _to_int_list(write_values, 'write_values')
        read_addr = _to_int(read_addr, 'read_addr')
        read_nb = _to_int(read_nb, 'read_nb')
        _check_addr_nb(write_addr, len(write_values), _MAX_WRITE_READ_REGS,
                       'write_addr', 'number of registers', 'write')
        _check_addr_nb(read_addr, read_nb, _MAX_READ_REGS, 'read_addr', 'read_nb')
        _check_regs_values(write_values, 'write_values')
        # make request
        try:
            # build PDU: header + registers part (pack all registers in one call)
            pdu_regs_part = struct.pack('>%dH' % len(write_values), *write_values)
            tx_pdu = struct.pack('>BHHHHB', WRITE_READ_MULTIPLE_REGISTERS, read_addr, read_nb,
                                 write_addr, len(write_values), len(pdu_regs_part))
            tx_pdu += pdu_regs_part
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            return self._decode_regs_reply(rx_pdu, read_nb)
        # handle error during request
        except ModbusClient._InternalError as e:
            self._req_except_handler(e)
            return None

    def _set_sock_timeout(self, value: float) -> None:
        """Set the current socket timeout, but only if it differs by more than 10 ms from the current one.

        Each settimeout() is a system call: in the usual case (response received at once) the timeout in place
        is already (almost) the right one, so there is nothing to do.

        :param value: socket timeout in seconds
        :type value: float
        """
        # skip for unset socket
        if self._sock is None:
            return
        # apply timeout if diff is great than 10ms
        current = self._sock.gettimeout()
        if current is None or abs(current - value) > 0.01:
            self._sock.settimeout(value)

    def _set_sock_tcp_no_delay(self, value: bool) -> None:
        # skip for unset socket
        if self._sock is None:
            return
        # apply tcp_nodelay value
        try:
            self._sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, int(value))
        except OSError as e:
            logger.debug('Unable to change TCP_NODELAY on the open connection: %r', e)

    def _send(self, frame: bytes) -> None:
        """Send frame over current socket.

        :param frame: modbus frame to send (MBAP + PDU)
        :type frame: bytes
        """
        # check socket status
        if self._sock is None or not self.is_open:
            raise ModbusClient._NetworkError(MB_SOCK_CLOSE_ERR, 'try to send on a close socket')
        # the timeout is a limit for the whole request (send + receive), not for each recv() call:
        # a slow server sending its response byte by byte can't hold the client more than timeout seconds
        self._deadline = time.monotonic() + self.timeout
        # send
        try:
            # restore the full timeout (it's reduced by _recv() to the time remaining in the previous request)
            self._set_sock_timeout(self.timeout)
            self._sock.sendall(frame)
        except socket.timeout:
            self.close()
            raise ModbusClient._NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except OSError:
            self.close()
            raise ModbusClient._NetworkError(MB_SEND_ERR, 'send error')

    def _send_pdu(self, pdu: bytes) -> None:
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
        self._on_tx_rx(frame=tx_frame, is_tx=True)

    def _recv(self, size: int) -> bytes:
        """Receive data over current socket (at most size bytes).

        :param size: max number of bytes to receive
        :type size: int
        :returns: receive data (never empty)
        :rtype: bytes
        :raises ModbusClient._NetworkError: on timeout, closed or broken connection
        """
        # check socket status
        if self._sock is None or not self.is_open:
            raise ModbusClient._NetworkError(MB_SOCK_CLOSE_ERR, 'try to recv on a close socket')
        # time remaining before the end of the request
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self.close()
            raise ModbusClient._NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        try:
            self._set_sock_timeout(remaining)
            r_buffer = self._sock.recv(size)
        except socket.timeout:
            self.close()
            raise ModbusClient._NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except OSError:
            r_buffer = b''
        # handle recv error
        if not r_buffer:
            self.close()
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'recv error')
        return r_buffer

    def _recv_all(self, size: int) -> bytes:
        """Receive data over current socket, loop until all bytes is received (avoid TCP frag).

        :param size: number of bytes to receive
        :type size: int
        :returns: receive data
        :rtype: bytes
        """
        r_buffer = b''
        while len(r_buffer) < size:
            r_buffer += self._recv(size - len(r_buffer))
        return r_buffer

    def _recv_pdu(self, func_code: int, min_len: int = 2) -> bytes:
        """Receive the modbus PDU (Protocol Data Unit).

        :param func_code: function code of the request (the reply must have the same)
        :type func_code: int
        :param min_len: minimal length of the PDU
        :type min_len: int
        :returns: modbus frame PDU
        :rtype: bytes
        """
        # receive 7 bytes header (MBAP)
        rx_mbap = self._recv_all(MBAP_SIZE)
        # decode MBAP
        (f_transaction_id, f_protocol_id, f_length, f_unit_id) = struct.unpack('>HHHB', rx_mbap)
        # check MBAP fields
        f_transaction_err = f_transaction_id != self._transaction_id
        f_protocol_err = f_protocol_id != 0
        f_length_err = not MBAP_MIN_LENGTH <= f_length <= MBAP_MAX_LENGTH
        f_unit_id_err = f_unit_id != self.unit_id
        # checking error status of fields: the stream can't be trusted anymore, close it
        if f_transaction_err or f_protocol_err or f_length_err or f_unit_id_err:
            self.close()
            self._on_tx_rx(frame=rx_mbap, is_tx=False)
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'MBAP checking error')
        # recv PDU
        rx_pdu = self._recv_all(f_length - 1)
        # for auto_close mode, close socket after each request
        if self.auto_close:
            self.close()
        # dump frame
        self._on_tx_rx(frame=rx_mbap + rx_pdu, is_tx=False)
        # body decode
        # check PDU length for global minimal frame (an except frame: func code + exp code)
        if len(rx_pdu) < 2:
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'PDU length is too short')
        # extract function code: it must be the one of the request (with or without the exception flag)
        rx_fc = rx_pdu[0]
        if rx_fc & 0x7f != func_code:
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'function code mismatch (0x%02X for a 0x%02X request)'
                                             % (rx_fc, func_code))
        # check except status
        if rx_fc >= 0x80:
            exp_code = rx_pdu[1]
            raise ModbusClient._ModbusExcept(exp_code)
        # check PDU length for specific request set in min_len (keep this after except checking)
        if len(rx_pdu) < min_len:
            raise ModbusClient._NetworkError(MB_RECV_ERR, 'PDU length is too short for current request')
        # if no error, return PDU
        return rx_pdu

    def _add_mbap(self, pdu: bytes) -> bytes:
        """Return full modbus frame with MBAP (modbus application protocol header) append to PDU.

        :param pdu: modbus PDU (protocol data unit)
        :type pdu: bytes
        :returns: full modbus frame
        :rtype: bytes
        """
        # build MBAP
        # next transaction ID (16 bits counter): it always differs from the previous request one, so a late reply
        # of the previous request (left on the socket) is always detected as such and never taken for the current one
        self._transaction_id = (self._transaction_id + 1) & 0xffff
        protocol_id = 0
        length = len(pdu) + 1
        mbap = struct.pack('>HHHB', self._transaction_id, protocol_id, length, self.unit_id)
        # full modbus/TCP frame = [MBAP]PDU
        return mbap + pdu

    def _req_pdu(self, tx_pdu: bytes, rx_min_len: int = 2) -> bytes:
        """Request processing (send and recv PDU).

        :param tx_pdu: modbus PDU (protocol data unit) to send
        :type tx_pdu: bytes
        :param rx_min_len: min length of receive PDU
        :type rx_min_len: int
        :returns: the receive PDU
        :rtype: bytes
        """
        # init request engine
        self._req_init()
        # send PDU
        self._send_pdu(tx_pdu)
        # return receive PDU
        return self._recv_pdu(func_code=tx_pdu[0], min_len=rx_min_len)

    def _req_init(self) -> None:
        """Reset request status flags."""
        self._last_error = MB_NO_ERR
        self._last_except = EXP_NONE

    def _req_except_handler(self, _except: Exception) -> None:
        """Global handler for internal exceptions."""
        # on request network error
        if isinstance(_except, ModbusClient._NetworkError):
            self._last_error = _except.code
            self._debug_msg(_except.message)
        # on request modbus except
        elif isinstance(_except, ModbusClient._ModbusExcept):
            self._last_error = MB_EXCEPT_ERR
            self._last_except = _except.code
            self._debug_msg(f'modbus exception (code {self.last_except} "{self.last_except_as_txt}")')

    def _debug_msg(self, msg: str) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f'({self.host}:{self.port}:{self.unit_id}) {msg}')

    def _on_tx_rx(self, frame: bytes, is_tx: bool) -> None:
        # format a log message
        if logger.isEnabledFor(logging.DEBUG):
            type_s = 'Tx' if is_tx else 'Rx'
            mbap_s = hexlify(frame[0:7], sep=' ').upper().decode()
            pdu_s = hexlify(frame[7:], sep=' ').upper().decode()
            self._debug_msg(f'{type_s} [{mbap_s}] {pdu_s}')
        # notify user
        self.on_tx_rx(frame=frame, is_tx=is_tx)

    def on_tx_rx(self, frame: bytes, is_tx: bool) -> None:
        """Call for each Tx/Rx (for user purposes)."""
        pass
