""" pyModbusTCP client: the Modbus functions (build the request PDU, decode the reply PDU). """

import struct
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    MAX_PDU_SIZE,
    MB_RECV_ERR,
    MEI_TYPE_READ_DEVICE_ID,
    READ_COILS,
    READ_DISCRETE_INPUTS,
    READ_HOLDING_REGISTERS,
    READ_INPUT_REGISTERS,
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


def _decode_regs(data: bytes, reg_nb: int) -> list[int]:
    """Decode the first reg_nb big-endian 16 bits registers of a modbus registers field."""
    return list(struct.unpack('>%dH' % reg_nb, data[:2 * reg_nb]))


class ClientFunctionsMixin:
    """Modbus functions of ModbusClient (one public method per function code).

    Each method checks its arguments, builds the request PDU, sends it with _req_pdu() and decodes the reply PDU.
    On error, the method returns None (or False) and the error is available with last_error/last_except.
    """

    if TYPE_CHECKING:
        # provided by ModbusClient
        def _req_pdu(self, tx_pdu: bytes, rx_min_len: int = 2) -> bytes: ...
        def _req_except_handler(self, _except: Exception) -> None: ...

    def custom_request(self, pdu: bytes | bytearray) -> bytes | None:
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
        except InternalError as e:
            self._req_except_handler(e)
            return None

    def _read_bits_req(self, func_code: int, bit_addr: int, bit_nb: int) -> list[bool] | None:
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
                raise NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
            # return bits list
            return _unpack_bits(rx_pdu_bits, bit_nb)
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return None

    def _read_regs_req(self, func_code: int, reg_addr: int, reg_nb: int) -> list[int] | None:
        """Common part of READ_HOLDING_REGISTERS and READ_INPUT_REGISTERS (params are already checked)."""
        try:
            tx_pdu = struct.pack('>BHH', func_code, reg_addr, reg_nb)
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=3)
            return self._decode_regs_reply(rx_pdu, reg_nb)
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return None

    @staticmethod
    def _decode_regs_reply(rx_pdu: bytes, reg_nb: int) -> list[int]:
        """Decode a [func code, byte count, registers...] reply PDU (functions 0x03, 0x04 and 0x17)."""
        # extract field "byte count"
        byte_count = rx_pdu[1]
        # frame with regs value
        f_regs = rx_pdu[2:]
        # check rx_byte_count: buffer size must be consistent and have at least the requested number of registers
        if byte_count < 2 * reg_nb or byte_count != len(f_regs):
            raise NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
        # return registers list
        return _decode_regs(f_regs, reg_nb)

    def _write_req(self, tx_pdu: bytes, addr: int, value: int) -> bool:
        """Send a write request and check that the reply echoes the [address, value or quantity] of the request."""
        try:
            rx_pdu = self._req_pdu(tx_pdu=tx_pdu, rx_min_len=5)
            resp_addr, resp_value = struct.unpack('>HH', rx_pdu[1:5])
            if resp_addr != addr or resp_value != value:
                raise NetworkError(MB_RECV_ERR, 'server reply does not match the request')
            return True
        # handle error during request
        except InternalError as e:
            self._req_except_handler(e)
            return False

    def read_coils(self, bit_addr: int, bit_nb: int = 1) -> list[bool] | None:
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
        check_addr_nb(bit_addr, bit_nb, MAX_READ_BITS, 'bit_addr', 'bit_nb')
        # make request
        return self._read_bits_req(READ_COILS, bit_addr, bit_nb)

    def read_discrete_inputs(self, bit_addr: int, bit_nb: int = 1) -> list[bool] | None:
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
        check_addr_nb(bit_addr, bit_nb, MAX_READ_BITS, 'bit_addr', 'bit_nb')
        # make request
        return self._read_bits_req(READ_DISCRETE_INPUTS, bit_addr, bit_nb)

    def read_holding_registers(self, reg_addr: int, reg_nb: int = 1) -> list[int] | None:
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
        check_addr_nb(reg_addr, reg_nb, MAX_READ_REGS, 'reg_addr', 'reg_nb')
        # make request
        return self._read_regs_req(READ_HOLDING_REGISTERS, reg_addr, reg_nb)

    def read_input_registers(self, reg_addr: int, reg_nb: int = 1) -> list[int] | None:
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
        check_addr_nb(reg_addr, reg_nb, MAX_READ_REGS, 'reg_addr', 'reg_nb')
        # make request
        return self._read_regs_req(READ_INPUT_REGISTERS, reg_addr, reg_nb)

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
                raise NetworkError(MB_RECV_ERR, 'server reply does not match the request')
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
                    raise NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
                obj_id = rx_pdu[pdu_offset]
                obj_len = rx_pdu[pdu_offset + 1]
                obj_value = rx_pdu[pdu_offset + 2:pdu_offset + 2 + obj_len]
                if obj_len != len(obj_value):
                    raise NetworkError(MB_RECV_ERR, 'rx byte count mismatch')
                # set offset to next object
                pdu_offset += 2 + obj_len
                # add result to request list
                response.objects_by_id[obj_id] = obj_value
            return response
        # handle error during request
        except InternalError as e:
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
        check_addr_nb(bits_addr, len(bits_value), MAX_WRITE_BITS, 'bits_addr', 'number of coils', 'write')
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
        check_addr_nb(regs_addr, len(regs_value), MAX_WRITE_REGS, 'regs_addr', 'number of registers', 'write')
        check_regs_values(regs_value, 'regs_value')
        # build PDU: header + registers part (pack all registers in one call)
        pdu_regs_part = struct.pack('>%dH' % len(regs_value), *regs_value)
        tx_pdu = struct.pack('>BHHB', WRITE_MULTIPLE_REGISTERS, regs_addr, len(regs_value), len(pdu_regs_part))
        tx_pdu += pdu_regs_part
        # make request
        return self._write_req(tx_pdu, regs_addr, len(regs_value))

    def write_read_multiple_registers(self, write_addr: int, write_values: Iterable[int],
                                      read_addr: int, read_nb: int = 1) -> list[int] | None:
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
        check_addr_nb(write_addr, len(write_values), MAX_WRITE_READ_REGS,
                      'write_addr', 'number of registers', 'write')
        check_addr_nb(read_addr, read_nb, MAX_READ_REGS, 'read_addr', 'read_nb')
        check_regs_values(write_values, 'write_values')
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
        except InternalError as e:
            self._req_except_handler(e)
            return None
