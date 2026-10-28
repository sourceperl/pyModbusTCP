""" pyModbusTCP client: the Modbus functions (build the request PDU, decode the reply PDU). """

import struct
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    MASK_WRITE_REGISTER,
    MAX_PDU_SIZE,
    MB_FRAME_ERR,
    MEI_TYPE_READ_DEVICE_ID,
    READ_COILS,
    READ_DISCRETE_INPUTS,
    READ_HOLDING_REGISTERS,
    READ_INPUT_REGISTERS,
    WRITE_FILE_RECORD,
    WRITE_MULTIPLE_COILS,
    WRITE_MULTIPLE_REGISTERS,
    WRITE_READ_MULTIPLE_REGISTERS,
    WRITE_SINGLE_COIL,
    WRITE_SINGLE_REGISTER,
)
from ..utils import (
    _pack_bits,
    _to_bool,
    _to_bool_list,
    _to_int,
    _to_int_list,
    _unpack_bits,
    byte_length,
)
from ._common import (
    MAX_READ_BITS,
    MAX_READ_REGS,
    MAX_WRITE_BITS,
    MAX_WRITE_READ_REGS,
    MAX_WRITE_REGS,
)
from ._errors import InternalError, NetworkError
from ._validators import check_addr_nb, check_regs_values
from .device_id import DeviceIdentificationResponse

# the functions that can be broadcast: they write data and need no reply (not 0x17, it reads too)
BROADCAST_FUNCTION_CODES = (WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS,
                            WRITE_FILE_RECORD, MASK_WRITE_REGISTER)


def _decode_regs(data: bytes, count: int) -> list[int]:
    """Decode the first count big-endian 16 bits registers of a modbus registers field."""
    return list(struct.unpack(f'>{count}H', data[:2 * count]))


class ClientFunctionsMixin:
    """Modbus functions of ModbusClient (one public method per function code).

    Each method checks its arguments, builds the request PDU, sends it with _req_pdu() and decodes the reply PDU.
    On error, the method returns None (or False) and the error is available with last_error/last_except.
    """

    if TYPE_CHECKING:
        # provided by ModbusClient
        def _req_pdu(self, tx_pdu: bytes, rx_min_len: int = 2) -> bytes: ...
        def _broadcast_req(self, tx_pdu: bytes) -> bool: ...
        def _req_except_handler(self, _except: Exception) -> None: ...

    def custom_request(self, pdu: bytes | bytearray, *, broadcast: bool = False) -> bytes | None:
        """Send a custom modbus request.

        The reply must have the same function code (or the same with the exception flag): otherwise it is an error.

        With broadcast=True, the request is sent to the unit id 0 (a write to every slave of a serial line, through
        a TCP to RTU gateway) without waiting for a reply: it returns the PDU sent (there is no reply PDU). Only for
        a function that writes data (0x05, 0x06, 0x0F, 0x10, 0x15, 0x16): ValueError otherwise.

        :param pdu: a modbus PDU (protocol data unit) of 1 to 253 bytes: function code followed by the request data
            (the function code is not checked, a code with the exception flag set (0x80 to 0xFF) is sent as is,
            but its reply can't be accepted: it's always a function code mismatch error)
        :type pdu: bytes or bytearray
        :param broadcast: send to the unit id 0 (every slave of a serial line, through a TCP to RTU gateway), without
            waiting for a reply: the PDU sent once the request is sent (default: False); a write function only
        :type broadcast: bool
        :returns: modbus frame PDU or None if error
        :rtype: bytes or None
        """
        # check params
        if not isinstance(pdu, (bytes, bytearray)):
            raise TypeError('pdu must be a bytes-like object')
        pdu = bytes(pdu)
        if not 1 <= len(pdu) <= MAX_PDU_SIZE:
            raise ValueError(f'pdu length out of range (valid from 1 to {MAX_PDU_SIZE} bytes)')
        if _to_bool(broadcast, 'broadcast'):
            if pdu[0] not in BROADCAST_FUNCTION_CODES:
                raise ValueError(f'function 0x{pdu[0]:02X} can\'t be broadcast (no reply: only a write function)')
            return pdu if self._broadcast_req(pdu) else None
        # make request
        try:
            return self._req_pdu(pdu)
        # handle errors during request
        except InternalError as e:
            self._req_except_handler(e)
            return None

    def _read_bits_req(self, func_code: int, address: int, count: int) -> list[bool] | None:
        """Common part of READ_COILS and READ_DISCRETE_INPUTS (params are already checked)."""
        try:
            tx_pdu = struct.pack('>BHH', func_code, address, count)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            # field "byte count" from PDU
            byte_count = rx_pdu[1]
            # bits PDU part
            rx_pdu_bits = rx_pdu[2:]
            # check rx_byte_count: exactly the bytes of the bits requested (more is the reply of another request,
            # like a late reply relayed by a gateway), and the size of the buffer
            if byte_count != byte_length(count) or byte_count != len(rx_pdu_bits):
                raise NetworkError(MB_FRAME_ERR, 'rx byte count mismatch')
            # return bits list
            return _unpack_bits(rx_pdu_bits, count)
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return None

    def _read_regs_req(self, func_code: int, address: int, count: int) -> list[int] | None:
        """Common part of READ_HOLDING_REGISTERS and READ_INPUT_REGISTERS (params are already checked)."""
        try:
            tx_pdu = struct.pack('>BHH', func_code, address, count)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            return self._decode_regs_reply(rx_pdu, count)
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return None

    @staticmethod
    def _decode_regs_reply(rx_pdu: bytes, count: int) -> list[int]:
        """Decode a [func code, byte count, registers...] reply PDU (functions 0x03, 0x04 and 0x17)."""
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with regs value
        f_regs = rx_pdu[2:]
        # check rx_byte_count: exactly the requested number of registers (more is the reply of another request, like a
        # late reply relayed by a gateway), and a consistent buffer size
        if byte_count != 2 * count or byte_count != len(f_regs):
            raise NetworkError(MB_FRAME_ERR, 'rx byte count mismatch')
        # return registers list
        return _decode_regs(f_regs, count)

    def _write_req(self, tx_pdu: bytes, addr: int, value: int) -> bool:
        """Send a write request and check that the reply echoes the [address, value or quantity] of the request."""
        try:
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=5)
            resp_addr, resp_value = struct.unpack('>HH', rx_pdu[1:5])
            if resp_addr != addr or resp_value != value:
                raise NetworkError(MB_FRAME_ERR, 'server reply does not match the request')
            return True
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return False

    def read_coils(self, address: int, count: int = 1) -> list[bool] | None:
        """Modbus function READ_COILS (0x01).

        :param address: address of the first bit (0 to 65535)
        :type address: int
        :param count: number of bits to read (1 to 2000)
        :type count: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        address = _to_int(address, 'address')
        count = _to_int(count, 'count')
        check_addr_nb(address, count, MAX_READ_BITS, 'address', 'count')
        # make request
        return self._read_bits_req(READ_COILS, address, count)

    def read_discrete_inputs(self, address: int, count: int = 1) -> list[bool] | None:
        """Modbus function READ_DISCRETE_INPUTS (0x02).

        :param address: address of the first bit (0 to 65535)
        :type address: int
        :param count: number of bits to read (1 to 2000)
        :type count: int
        :returns: bits list or None if error
        :rtype: list of bool or None
        """
        # check params
        address = _to_int(address, 'address')
        count = _to_int(count, 'count')
        check_addr_nb(address, count, MAX_READ_BITS, 'address', 'count')
        # make request
        return self._read_bits_req(READ_DISCRETE_INPUTS, address, count)

    def read_holding_registers(self, address: int, count: int = 1) -> list[int] | None:
        """Modbus function READ_HOLDING_REGISTERS (0x03).

        :param address: address of the first register (0 to 65535)
        :type address: int
        :param count: number of registers to read (1 to 125)
        :type count: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        address = _to_int(address, 'address')
        count = _to_int(count, 'count')
        check_addr_nb(address, count, MAX_READ_REGS, 'address', 'count')
        # make request
        return self._read_regs_req(READ_HOLDING_REGISTERS, address, count)

    def read_input_registers(self, address: int, count: int = 1) -> list[int] | None:
        """Modbus function READ_INPUT_REGISTERS (0x04).

        :param address: address of the first register (0 to 65535)
        :type address: int
        :param count: number of registers to read (1 to 125)
        :type count: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        address = _to_int(address, 'address')
        count = _to_int(count, 'count')
        check_addr_nb(address, count, MAX_READ_REGS, 'address', 'count')
        # make request
        return self._read_regs_req(READ_INPUT_REGISTERS, address, count)

    def read_device_identification(self, read_code: int = 1,
                                   object_id: int = 0) -> DeviceIdentificationResponse | None:
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
                raise NetworkError(MB_FRAME_ERR, 'server reply does not match the request')
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
                    raise NetworkError(MB_FRAME_ERR, 'rx byte count mismatch')
                obj_id = rx_pdu[pdu_offset]
                obj_len = rx_pdu[pdu_offset + 1]
                obj_value = rx_pdu[pdu_offset + 2:pdu_offset + 2 + obj_len]
                if obj_len != len(obj_value):
                    raise NetworkError(MB_FRAME_ERR, 'rx byte count mismatch')
                # set offset to next object
                pdu_offset += 2 + obj_len
                # add result to request list
                response.objects_by_id[obj_id] = obj_value
            # nothing after the last object (more is not this reply, like another frame relayed by a gateway)
            if pdu_offset != len(rx_pdu):
                raise NetworkError(MB_FRAME_ERR, 'rx byte count mismatch')
            return response
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return None

    def write_single_coil(self, address: int, value: bool, *, broadcast: bool = False) -> bool:
        """Modbus function WRITE_SINGLE_COIL (0x05).

        :param address: address of the first bit (0 to 65535)
        :type address: int
        :param value: the coil value to write
        :type value: bool
        :param broadcast: send to the unit id 0 (every slave of a serial line, through a TCP to RTU gateway), without
            waiting for a reply: True once the request is sent (default: False)
        :type broadcast: bool
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        address = _to_int(address, 'address')
        value = _to_bool(value, 'value')
        if not 0 <= address <= 0xffff:
            raise ValueError('address out of range (valid from 0 to 65535)')
        # format "bit value" field for PDU
        value_raw = 0xff00 if value else 0x0000
        # make request
        tx_pdu = struct.pack('>BHH', WRITE_SINGLE_COIL, address, value_raw)
        if _to_bool(broadcast, 'broadcast'):
            return self._broadcast_req(tx_pdu)
        return self._write_req(tx_pdu, address, value_raw)

    def write_single_register(self, address: int, value: int, *, broadcast: bool = False) -> bool:
        """Modbus function WRITE_SINGLE_REGISTER (0x06).

        :param address: address of the first register (0 to 65535)
        :type address: int
        :param value: the register value to write (0 to 65535)
        :type value: int
        :param broadcast: send to the unit id 0 (every slave of a serial line, through a TCP to RTU gateway), without
            waiting for a reply: True once the request is sent (default: False)
        :type broadcast: bool
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        address = _to_int(address, 'address')
        value = _to_int(value, 'value')
        if not 0 <= address <= 0xffff:
            raise ValueError('address out of range (valid from 0 to 65535)')
        if not 0 <= value <= 0xffff:
            raise ValueError('value out of range (valid from 0 to 65535)')
        # make request
        tx_pdu = struct.pack('>BHH', WRITE_SINGLE_REGISTER, address, value)
        if _to_bool(broadcast, 'broadcast'):
            return self._broadcast_req(tx_pdu)
        return self._write_req(tx_pdu, address, value)

    def write_multiple_coils(self, address: int, values: Iterable[int], *, broadcast: bool = False) -> bool:
        """Modbus function WRITE_MULTIPLE_COILS (0x0F).

        :param address: address of the first coil (0 to 65535)
        :type address: int
        :param values: the coil values to write, 1 to 1968 (any iterable of bool or int)
        :type values: list
        :param broadcast: send to the unit id 0 (every slave of a serial line, through a TCP to RTU gateway), without
            waiting for a reply: True once the request is sent (default: False)
        :type broadcast: bool
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        address = _to_int(address, 'address')
        values = _to_bool_list(values, 'values')
        check_addr_nb(address, len(values), MAX_WRITE_BITS, 'address', 'number of values', 'write')
        # build PDU: header + coils part (pack all the bits in bytes at once)
        pdu_coils_part = _pack_bits(values)
        tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_COILS, address, len(values), len(pdu_coils_part))
        tx_pdu += pdu_coils_part
        # make request
        if _to_bool(broadcast, 'broadcast'):
            return self._broadcast_req(tx_pdu)
        return self._write_req(tx_pdu, address, len(values))

    def write_multiple_registers(self, address: int, values: Iterable[int], *, broadcast: bool = False) -> bool:
        """Modbus function WRITE_MULTIPLE_REGISTERS (0x10).

        :param address: address of the first register (0 to 65535)
        :type address: int
        :param values: the register values to write, 1 to 123 (any iterable of int, 0 to 65535)
        :type values: list
        :param broadcast: send to the unit id 0 (every slave of a serial line, through a TCP to RTU gateway), without
            waiting for a reply: True once the request is sent (default: False)
        :type broadcast: bool
        :returns: True if write ok
        :rtype: bool
        """
        # check params
        address = _to_int(address, 'address')
        values = _to_int_list(values, 'values')
        check_addr_nb(address, len(values), MAX_WRITE_REGS, 'address', 'number of values', 'write')
        check_regs_values(values, 'values')
        # build PDU: header + registers part (pack all registers in one call)
        pdu_regs_part = struct.pack(f'>{len(values)}H', *values)
        tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_REGISTERS, address, len(values), len(pdu_regs_part))
        tx_pdu += pdu_regs_part
        # make request
        if _to_bool(broadcast, 'broadcast'):
            return self._broadcast_req(tx_pdu)
        return self._write_req(tx_pdu, address, len(values))

    def write_read_multiple_registers(self, write_address: int, write_values: Iterable[int],
                                      read_address: int, read_count: int = 1) -> list[int] | None:
        """Modbus function WRITE_READ_MULTIPLE_REGISTERS (0x17).

        :param write_address: address of the first register to write (0 to 65535)
        :type write_address: int
        :param write_values: the register values to write, 1 to 121 (any iterable of int, 0 to 65535)
        :type write_values: list
        :param read_address: address of the first register to read (0 to 65535)
        :type read_address: int
        :param read_count: number of registers to read (1 to 125)
        :type read_count: int
        :returns: registers list or None if fail
        :rtype: list of int or None
        """
        # check params
        write_address = _to_int(write_address, 'write_address')
        write_values = _to_int_list(write_values, 'write_values')
        read_address = _to_int(read_address, 'read_address')
        read_count = _to_int(read_count, 'read_count')
        check_addr_nb(write_address, len(write_values), MAX_WRITE_READ_REGS,
                      'write_address', 'number of values', 'write')
        check_addr_nb(read_address, read_count, MAX_READ_REGS, 'read_address', 'read_count')
        check_regs_values(write_values, 'write_values')
        # make request
        try:
            # build PDU: header + registers part (pack all registers in one call)
            pdu_regs_part = struct.pack(f'>{len(write_values)}H', *write_values)
            tx_pdu = struct.pack('>BHHHHB', WRITE_READ_MULTIPLE_REGISTERS, read_address, read_count,
                                 write_address, len(write_values), len(pdu_regs_part))
            tx_pdu += pdu_regs_part
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            return self._decode_regs_reply(rx_pdu, read_count)
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return None
