""" pyModbusTCP server: implementation of the Modbus functions of the internal engine. """

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any, ClassVar

from ..constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    EXP_DATA_ADDRESS,
    EXP_DATA_VALUE,
    EXP_SLAVE_DEVICE_FAILURE,
    MAX_PDU_SIZE,
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
from ..utils import _pack_bits, _unpack_bits
from ._common import logger
from .data_handler import DataHandler
from .device_id import DeviceIdentification
from .session import SessionData


class ServerFunctionsMixin:
    """Modbus functions of the ModbusServer internal engine (one method per function code).

    Each method decodes the request PDU of the session, calls the data handler and builds the response PDU.
    """

    # read device identification, stream access: ReadDevId code -> (first, last) object id of its category
    _DEVICE_ID_STREAM_RANGES: ClassVar[dict[int, tuple[int, int]]] = {1: (0x00, 0x02), 2: (0x03, 0x7f), 3: (0x80, 0xff)}

    # set by ModbusServer.__init__()
    data_hdl: DataHandler
    device_id: DeviceIdentification | None

    def _build_func_map(self) -> dict[int, Callable[[SessionData], None]]:
        """Return the routing table: Modbus function code -> method that processes it."""
        return {
            READ_COILS: self._read_bits,
            READ_DISCRETE_INPUTS: self._read_bits,
            READ_HOLDING_REGISTERS: self._read_words,
            READ_INPUT_REGISTERS: self._read_words,
            WRITE_SINGLE_COIL: self._write_single_coil,
            WRITE_SINGLE_REGISTER: self._write_single_register,
            WRITE_MULTIPLE_COILS: self._write_multiple_coils,
            WRITE_MULTIPLE_REGISTERS: self._write_multiple_registers,
            WRITE_READ_MULTIPLE_REGISTERS: self._write_read_multiple_registers,
            ENCAPSULATED_INTERFACE_TRANSPORT: self._encapsulated_interface_transport
        }

    @staticmethod
    def _checked_read_data(func_code: int, expected: int, data: list[Any] | None) -> list[Any] | None:
        """Return data if a data handler returned the expected number of items for a read request.

        Otherwise log an error and return None (the caller must answer with a slave device failure).
        """
        if data is None or len(data) != expected:
            logger.error('Data handler returned %s item(s) for function 0x%02X, %d expected',
                         'no' if data is None else len(data), func_code, expected)
            return None
        return data

    def _read_bits(self, session_data: SessionData) -> None:
        """
        Functions Read Coils (0x01) or Read Discrete Inputs (0x02).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (start_address, quantity_bits) = recv_pdu.unpack('>HH', from_byte=1, to_byte=5)
        # check quantity of requested bits
        if 0x0001 <= quantity_bits <= 0x07D0:
            # data handler read request: for coils or discrete inputs space
            if recv_pdu.func_code == READ_COILS:
                ret_hdl = self.data_hdl.read_coils(start_address, quantity_bits, session_data.srv_info)
            else:
                ret_hdl = self.data_hdl.read_d_inputs(start_address, quantity_bits, session_data.srv_info)
            # format regular or except response
            if not ret_hdl.ok:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
            else:
                bits_l = self._checked_read_data(recv_pdu.func_code, quantity_bits, ret_hdl.data)
                if bits_l is None:
                    send_pdu.build_except(recv_pdu.func_code, EXP_SLAVE_DEVICE_FAILURE)
                else:
                    # pack data bank bits in bytes
                    bytes_b = _pack_bits(bits_l)
                    # build pdu
                    send_pdu.add_pack(f'BB{len(bytes_b)}s', recv_pdu.func_code, len(bytes_b), bytes_b)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _read_words(self, session_data: SessionData) -> None:
        """
        Functions Read Holding Registers (0x03) or Read Input Registers (0x04).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (start_addr, quantity_regs) = recv_pdu.unpack('>HH', from_byte=1, to_byte=5)
        # check quantity of requested words
        if 0x0001 <= quantity_regs <= 0x007D:
            # data handler read request: for holding or input registers space
            if recv_pdu.func_code == READ_HOLDING_REGISTERS:
                ret_hdl = self.data_hdl.read_h_regs(start_addr, quantity_regs, session_data.srv_info)
            else:
                ret_hdl = self.data_hdl.read_i_regs(start_addr, quantity_regs, session_data.srv_info)
            # format regular or except response
            if not ret_hdl.ok:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
            else:
                words_l = self._checked_read_data(recv_pdu.func_code, quantity_regs, ret_hdl.data)
                if words_l is None:
                    send_pdu.build_except(recv_pdu.func_code, EXP_SLAVE_DEVICE_FAILURE)
                else:
                    # build pdu
                    # (header + requested words in a single pack: avoid an intermediate bytes copy)
                    send_pdu.add_pack(f'>BB{len(words_l)}H', recv_pdu.func_code, quantity_regs * 2, *words_l)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_single_coil(self, session_data: SessionData) -> None:
        """
        Function Write Single Coil (0x05).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (coil_addr, coil_value) = recv_pdu.unpack('>HH', from_byte=1, to_byte=5)
        # check allowed values
        if coil_value not in (0x0000, 0xFF00):
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)
            return
        # format coil raw value to bool
        coil_as_bool = bool(coil_value == 0xFF00)
        # data handler update request
        ret_hdl = self.data_hdl.write_coils(coil_addr, [coil_as_bool], session_data.srv_info)
        # format except or regular response
        if not ret_hdl.ok:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
            return
        send_pdu.add_pack('>BHH', recv_pdu.func_code, coil_addr, coil_value)

    def _write_single_register(self, session_data: SessionData) -> None:
        """
        Functions Write Single Register (0x06).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (reg_addr, reg_value) = recv_pdu.unpack('>HH', from_byte=1, to_byte=5)
        # data handler update request
        ret_hdl = self.data_hdl.write_h_regs(reg_addr, [reg_value], session_data.srv_info)
        # format regular or except response
        if ret_hdl.ok:
            send_pdu.add_pack('>BHH', recv_pdu.func_code, reg_addr, reg_value)
        else:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)

    def _write_multiple_coils(self, session_data: SessionData) -> None:
        """
        Function Write Multiple Coils (0x0F).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (start_addr, quantity_bits, byte_count) = recv_pdu.unpack('>HHB', from_byte=1, to_byte=6)
        # ok flags: some tests on pdu fields
        # (byte count must match the quantity, the data must be complete, any extra bytes after them are ignored)
        qty_bits_ok = 0x0001 <= quantity_bits <= 0x07B0
        b_count_ok = byte_count == (quantity_bits + 7) // 8
        pdu_len_ok = len(recv_pdu) >= 6 + byte_count
        # test ok flags
        if qty_bits_ok and b_count_ok and pdu_len_ok:
            # bits list from rx frame
            bits_l = _unpack_bits(recv_pdu.raw[6:], quantity_bits)
            # data handler update request
            ret_hdl = self.data_hdl.write_coils(start_addr, bits_l, session_data.srv_info)
            # format regular or except response
            if ret_hdl.ok:
                send_pdu.add_pack('>BHH', recv_pdu.func_code, start_addr, quantity_bits)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_multiple_registers(self, session_data: SessionData) -> None:
        """
        Function Write Multiple Registers (0x10).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (start_addr, quantity_regs, byte_count) = recv_pdu.unpack('>HHB', from_byte=1, to_byte=6)
        # ok flags: some tests on pdu fields
        # (byte count must match the quantity, the data must be complete, any extra bytes after them are ignored)
        qty_regs_ok = 0x0001 <= quantity_regs <= 0x007B
        b_count_ok = byte_count == quantity_regs * 2
        pdu_len_ok = len(recv_pdu) >= 6 + byte_count
        # test ok flags
        if qty_regs_ok and b_count_ok and pdu_len_ok:
            # words list from rx frame
            regs_l = list(recv_pdu.unpack(f'>{quantity_regs}H', from_byte=6, to_byte=6 + byte_count))
            # data handler update request
            ret_hdl = self.data_hdl.write_h_regs(start_addr, regs_l, session_data.srv_info)
            # format regular or except response
            if ret_hdl.ok:
                send_pdu.add_pack('>BHH', recv_pdu.func_code, start_addr, quantity_regs)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_read_multiple_registers(self, session_data: SessionData) -> None:
        """
        Function Write Read Multiple Registers (0x17).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (read_start_addr,
         read_quantity_regs,
         write_start_addr,
         write_quantity_regs,
         byte_count) = recv_pdu.unpack('>HHHHB', from_byte=1, to_byte=10)
        # ok flags: some tests on pdu fields
        # (byte count must match the quantity, the data must be complete, any extra bytes after them are ignored)
        write_qty_regs_ok = 0x0001 <= write_quantity_regs <= 0x0079
        write_b_count_ok = byte_count == write_quantity_regs * 2
        write_pdu_len_ok = len(recv_pdu) >= 10 + byte_count
        read_qty_regs_ok = 0x0001 <= read_quantity_regs <= 0x007D
        # test ok flags
        if not (write_qty_regs_ok and write_b_count_ok and write_pdu_len_ok and read_qty_regs_ok):
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)
            return
        # words list from rx frame
        regs_l = list(recv_pdu.unpack(f'>{write_quantity_regs}H', from_byte=10, to_byte=10 + byte_count))
        # gratuitous read to check error status (avoid to write if read fail)
        ret_hdl_read = self.data_hdl.read_h_regs(read_start_addr, read_quantity_regs, session_data.srv_info)
        if not ret_hdl_read.ok:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl_read.exp_code)
            return
        # a handler that returns "ok" without the requested data: never answer with the (invalid) exception code 0
        if self._checked_read_data(recv_pdu.func_code, read_quantity_regs, ret_hdl_read.data) is None:
            send_pdu.build_except(recv_pdu.func_code, EXP_SLAVE_DEVICE_FAILURE)
            return
        # do write
        ret_hdl_write = self.data_hdl.write_h_regs(write_start_addr, regs_l, session_data.srv_info)
        if not ret_hdl_write.ok:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl_write.exp_code)
            return
        # redo read after write
        ret_hdl_read = self.data_hdl.read_h_regs(read_start_addr, read_quantity_regs, session_data.srv_info)
        if not ret_hdl_read.ok:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl_read.exp_code)
            return
        read_words_l = self._checked_read_data(recv_pdu.func_code, read_quantity_regs, ret_hdl_read.data)
        if read_words_l is None:
            send_pdu.build_except(recv_pdu.func_code, EXP_SLAVE_DEVICE_FAILURE)
            return
        # build pdu
        send_pdu.add_pack('BB', recv_pdu.func_code, read_quantity_regs * 2)
        # add_pack requested words
        send_pdu.add_pack(f'>{len(read_words_l)}H', *read_words_l)

    def _encapsulated_interface_transport(self, session_data: SessionData) -> None:
        """
        Modbus Encapsulated Interface transport (MEI) endpoint (0x2B).

        :param session_data: server engine data
        :type session_data: SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (mei_type,) = recv_pdu.unpack('B', from_byte=1, to_byte=2)
        # MEI type: only read device identification is supported, return except 2 for an unknown one
        if mei_type != MEI_TYPE_READ_DEVICE_ID:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_ADDRESS)
            return
        (device_id_code, object_id) = recv_pdu.unpack('BB', from_byte=2, to_byte=4)
        # return except 3 for unknown device id code
        if device_id_code not in (1, 2, 3, 4):
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)
            return
        # check device_id property is set (default is None), return except 2 if unset
        if self.device_id is None:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_ADDRESS)
            return
        if device_id_code == 4:
            # individual access: an unknown object is an illegal data address (except 2)
            if object_id not in self.device_id:
                send_pdu.build_except(recv_pdu.func_code, EXP_DATA_ADDRESS)
                return
            req_objects_l = self.device_id.items(start=object_id, end=object_id)
        else:
            # stream access: basic (code 1), regular (code 2) or extended (code 3) objects
            first_id, last_id = self._DEVICE_ID_STREAM_RANGES[device_id_code]
            # an object id that is not a known object of the category: restart at its first object (as the spec
            # requires for object 0)
            if not (first_id <= object_id <= last_id and object_id in self.device_id):
                object_id = first_id
            req_objects_l = self.device_id.items(start=object_id, end=last_id)
        # init variables for response PDU build
        conformity_level = 0x83
        more_follow = 0
        next_obj_id = 0
        number_of_objs = 0
        fmt_pdu_head = 'BBBBBBB'
        # format objects data part = [[obj id, obj len, obj val], ...]
        obj_data_part = b''
        for req_obj_id, req_obj_value in req_objects_l:
            fmt_obj_blk = f'BB{len(req_obj_value)}s'
            # skip if the next add to data part will exceed max PDU size of modbus frame
            if struct.calcsize(fmt_pdu_head) + len(obj_data_part) + struct.calcsize(fmt_obj_blk) > MAX_PDU_SIZE:
                # turn on "more follow" field and set "next object id" field with next object id to ask
                more_follow = 0xff
                next_obj_id = req_obj_id
                break
            # add current object to data part (DeviceIdentification only stores bytes)
            obj_data_part += struct.pack(fmt_obj_blk, req_obj_id, len(req_obj_value), req_obj_value)
            number_of_objs += 1
        # full PDU response = [PDU header] + [objects data part]
        send_pdu.add_pack(fmt_pdu_head, recv_pdu.func_code, mei_type, device_id_code,
                          conformity_level, more_follow, next_obj_id, number_of_objs)
        send_pdu.raw += obj_data_part
