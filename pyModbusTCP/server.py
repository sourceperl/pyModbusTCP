""" pyModbusTCP Server """

from __future__ import annotations

import errno
import logging
import os
import socket
import struct
import time
from socketserver import BaseRequestHandler, ThreadingTCPServer
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    EXP_DATA_ADDRESS,
    EXP_DATA_VALUE,
    EXP_ILLEGAL_FUNCTION,
    EXP_NONE,
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
from .utils import _pack_bits, _to_bool_list, _to_int_list, _unpack_bits

# add a logger for pyModbusTCP.server
logger = logging.getLogger(__name__)


class DataBank:
    """ Data space class with thread safe access functions """

    def __init__(self, coils_size: int = 0x10000, coils_default_value: bool = False,
                 d_inputs_size: int = 0x10000, d_inputs_default_value: bool = False,
                 h_regs_size: int = 0x10000, h_regs_default_value: int = 0,
                 i_regs_size: int = 0x10000, i_regs_default_value: int = 0,
                 virtual_mode: bool = False) -> None:
        """Constructor

        Modbus server data bank constructor.

        :param coils_size: Number of coils to allocate (default is 65536)
        :type coils_size: int
        :param coils_default_value: Coils default value at startup (default is False)
        :type coils_default_value: bool
        :param d_inputs_size: Number of discrete inputs to allocate (default is 65536)
        :type d_inputs_size: int
        :param d_inputs_default_value: Discrete inputs default value at startup (default is False)
        :type d_inputs_default_value: bool
        :param h_regs_size: Number of holding registers to allocate (default is 65536)
        :type h_regs_size: int
        :param h_regs_default_value: Holding registers default value at startup (default is 0)
        :type h_regs_default_value: int
        :param i_regs_size: Number of input registers to allocate (default is 65536)
        :type i_regs_size: int
        :param i_regs_default_value: Input registers default value at startup (default is 0)
        :type i_regs_default_value: int
        :param virtual_mode: Disallow all modbus data space to work with virtual values (default is False)
        :type virtual_mode: bool
        """
        # public
        self.coils_size = int(coils_size)
        self.coils_default_value = bool(coils_default_value)
        self.d_inputs_size = int(d_inputs_size)
        self.d_inputs_default_value = bool(d_inputs_default_value)
        self.h_regs_size = int(h_regs_size)
        self.h_regs_default_value = int(h_regs_default_value)
        self.i_regs_size = int(i_regs_size)
        self.i_regs_default_value = int(i_regs_default_value)
        self.virtual_mode = virtual_mode
        # specific modes (override some values)
        if self.virtual_mode:
            self.coils_size = 0
            self.d_inputs_size = 0
            self.h_regs_size = 0
            self.i_regs_size = 0
        # private
        self._coils_lock = Lock()
        self._coils = [self.coils_default_value] * self.coils_size
        self._d_inputs_lock = Lock()
        self._d_inputs = [self.d_inputs_default_value] * self.d_inputs_size
        self._h_regs_lock = Lock()
        self._h_regs = [self.h_regs_default_value] * self.h_regs_size
        self._i_regs_lock = Lock()
        self._i_regs = [self.i_regs_default_value] * self.i_regs_size

    def __repr__(self) -> str:
        attrs_str = ''
        for attr_name in self.__dict__:
            if isinstance(attr_name, str) and not attr_name.startswith('_'):
                if attrs_str:
                    attrs_str += ', '
                attrs_str += '%s=%r' % (attr_name, self.__dict__[attr_name])
        return 'DataBank(%s)' % attrs_str

    def get_coils(self, address: int, number: int = 1,
                  srv_info: Optional[ModbusServer.ServerInfo] = None) -> Optional[List[bool]]:
        """Read data on server coils space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServer.ServerInfo
        :returns: list of bool or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._coils_lock:
            if (address >= 0) and (address + number <= len(self._coils)):
                return self._coils[address: number + address]
            else:
                return None

    def set_coils(self, address: int, bit_list: List[bool],
                  srv_info: Optional[ModbusServer.ServerInfo] = None) -> Optional[bool]:
        """Write data to server coils space

        :param address: start address
        :type address: int
        :param bit_list: a list of bool to write
        :type bit_list: list
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServerInfo
        :returns: True if success or None if error
        :rtype: bool or None
        :raises TypeError: if bit_list members are not booleans or integers
        """
        # ensure bit_list values are bool
        bit_list = _to_bool_list(bit_list, 'bit_list')
        # keep trace of any changes
        changes_list = []
        # ensure atomic update of internal data
        with self._coils_lock:
            end = address + len(bit_list)
            if (address >= 0) and (end <= len(self._coils)):
                # compare the whole slice at C speed: skip everything if nothing changes
                old_values = self._coils[address:end]
                if old_values != bit_list:
                    # changes are only listed when a callback will use them (server request)
                    if srv_info:
                        changes_list = [(address + i, old, new) for i, (old, new)
                                        in enumerate(zip(old_values, bit_list)) if old != new]
                    self._coils[address:end] = bit_list
            else:
                return None
        # on server update
        if srv_info:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_coils_change(address, from_value, to_value, srv_info)
        return True

    def get_discrete_inputs(self, address: int, number: int = 1,
                            srv_info: Optional[ModbusServer.ServerInfo] = None) -> Optional[List[bool]]:
        """Read data on server discrete inputs space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServerInfo
        :returns: list of bool or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        end = address + number
        with self._d_inputs_lock:
            if (address >= 0) and (end <= len(self._d_inputs)):
                return self._d_inputs[address: number + address]
            else:
                return None

    def set_discrete_inputs(self, address: int, bit_list: List[bool]) -> Optional[bool]:
        """Write data to server discrete inputs space

        :param address: start address
        :type address: int
        :param bit_list: a list of bool to write
        :type bit_list: list
        :returns: True if success or None if error
        :rtype: bool or None
        :raises TypeError: if bit_list members are not booleans or integers
        """
        # ensure bit_list values are bool
        bit_list = _to_bool_list(bit_list, 'bit_list')
        end = address + len(bit_list)
        # ensure atomic update of internal data
        with self._d_inputs_lock:
            if (address >= 0) and (end <= len(self._d_inputs)):
                self._d_inputs[address:address + len(bit_list)] = bit_list
            else:
                return None
        return True

    def get_holding_registers(self, address: int, number: int = 1,
                              srv_info: Optional[ModbusServer.ServerInfo] = None) -> Optional[List[int]]:
        """Read data on server holding registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServerInfo
        :returns: list of int or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._h_regs_lock:
            if (address >= 0) and (address + number <= len(self._h_regs)):
                return self._h_regs[address: number + address]
            else:
                return None

    def set_holding_registers(self, address: int, word_list: List[int],
                              srv_info: Optional[ModbusServer.ServerInfo] = None) -> Optional[bool]:
        """Write data to server holding registers space

        :param address: start address
        :type address: int
        :param word_list: a list of word to write
        :type word_list: list
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServerInfo
        :returns: True if success or None if error
        :rtype: bool or None
        :raises TypeError: if word_list members are not integers
        :raises ValueError: if word_list members are out of the 16 bits range (0 to 65535)
        """
        # ensure word_list values are int with a max bit length of 16
        word_list = _to_int_list(word_list, 'word_list')
        if word_list and (min(word_list) < 0 or max(word_list) > 0xffff):
            raise ValueError('word_list list contains out of range values')
        end = address + len(word_list)
        # keep trace of any changes
        changes_list = []
        # ensure atomic update of internal data
        with self._h_regs_lock:
            if (address >= 0) and (end <= len(self._h_regs)):
                # compare the whole slice at C speed: skip everything if nothing changes
                old_values = self._h_regs[address:end]
                if old_values != word_list:
                    # changes are only listed when a callback will use them (server request)
                    if srv_info:
                        changes_list = [(address + i, old, new) for i, (old, new)
                                        in enumerate(zip(old_values, word_list)) if old != new]
                    self._h_regs[address:end] = word_list
            else:
                return None
        # on server update
        if srv_info:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_holding_registers_change(address, from_value, to_value, srv_info=srv_info)
        return True

    def get_input_registers(self, address: int, number: int = 1,
                            srv_info: Optional[ModbusServer.ServerInfo] = None) -> Optional[List[int]]:
        """Read data on server input registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServerInfo
        :returns: list of int or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._i_regs_lock:
            if (address >= 0) and (address + number <= len(self._i_regs)):
                return self._i_regs[address: number + address]
            else:
                return None

    def set_input_registers(self, address: int, word_list: List[int]) -> Optional[bool]:
        """Write data to server input registers space

        :param address: start address
        :type address: int
        :param word_list: a list of word to write
        :type word_list: list
        :returns: True if success or None if error
        :rtype: bool or None
        :raises TypeError: if word_list members are not integers
        :raises ValueError: if word_list members are out of the 16 bits range (0 to 65535)
        """
        # ensure word_list values are int with a max bit length of 16
        word_list = _to_int_list(word_list, 'word_list')
        if word_list and (min(word_list) < 0 or max(word_list) > 0xffff):
            raise ValueError('word_list list contains out of range values')
        end = address + len(word_list)
        # ensure atomic update of internal data
        with self._i_regs_lock:
            if (address >= 0) and (end <= len(self._i_regs)):
                self._i_regs[address:address + len(word_list)] = word_list
            else:
                return None
        return True

    def on_coils_change(self, address: int, from_value: bool, to_value: bool,
                        srv_info: ModbusServer.ServerInfo) -> None:
        """Call by server when a value change occur in coils space

        This method is provided to be overridden with user code to catch changes

        :param address: address of coil
        :type address: int
        :param from_value: coil original value
        :type from_value: bool
        :param to_value: coil next value
        :type to_value: bool
        :param srv_info: some server info
        :type srv_info: ModbusServerInfo
        """
        pass

    def on_holding_registers_change(self, address: int, from_value: int, to_value: int,
                                    srv_info: ModbusServer.ServerInfo) -> None:
        """Call by server when a value change occur in holding registers space

        This method is provided to be overridden with user code to catch changes

        :param address: address of register
        :type address: int
        :param from_value: register original value
        :type from_value: int
        :param to_value: register next value
        :type to_value: int
        :param srv_info: some server info
        :type srv_info: ModbusServerInfo
        """
        pass


class DataHandler:
    """Default data handler for ModbusServer, map server threads calls to DataBank.

    Custom handler must derive from this class.
    """

    class Return:
        def __init__(self, exp_code: int, data: Optional[List[Any]] = None) -> None:
            self.exp_code = exp_code
            self.data = data

        @property
        def ok(self) -> bool:
            return self.exp_code == EXP_NONE

    def __init__(self, data_bank: Optional[DataBank] = None) -> None:
        """Constructor

        Modbus server data handler constructor.

        :param data_bank: a reference to custom DefaultDataBank
        :type data_bank: DataBank
        """
        # check data_bank type
        if data_bank and not isinstance(data_bank, DataBank):
            raise TypeError('data_bank arg is invalid')
        # public
        self.data_bank = data_bank or DataBank()

    def __repr__(self) -> str:
        return 'ModbusServerDataHandler(data_bank=%s)' % self.data_bank

    def read_coils(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        """Call by server for reading in coils space

        :param address: start address
        :type address: int
        :param count: number of coils
        :type count: int
        :param srv_info: some server info
        :type srv_info: ModbusServer.ServerInfo
        :rtype: Return
        """
        # read bits from DataBank
        bits_l = self.data_bank.get_coils(address, count, srv_info)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def write_coils(self, address: int, bits_l: List[bool], srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        """Call by server for writing in the coils space

        :param address: start address
        :type address: int
        :param bits_l: list of boolean to write
        :type bits_l: list
        :param srv_info: some server info
        :type srv_info: ModbusServer.ServerInfo
        :rtype: Return
        """
        # write bits to DataBank
        update_ok = self.data_bank.set_coils(address, bits_l, srv_info)
        # return DataStatus to server
        if update_ok:
            return DataHandler.Return(exp_code=EXP_NONE)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def read_d_inputs(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        """Call by server for reading in the discrete inputs space

        :param address: start address
        :type address: int
        :param count: number of discrete inputs
        :type count: int
        :param srv_info: some server info
        :type srv_info: ModbusServer.ServerInfo
        :rtype: Return
        """
        # read bits from DataBank
        bits_l = self.data_bank.get_discrete_inputs(address, count, srv_info)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def read_h_regs(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        """Call by server for reading in the holding registers space

        :param address: start address
        :type address: int
        :param count: number of holding registers
        :type count: int
        :param srv_info: some server info
        :type srv_info: ModbusServer.ServerInfo
        :rtype: Return
        """
        # read words from DataBank
        words_l = self.data_bank.get_holding_registers(address, count, srv_info)
        # return DataStatus to server
        if words_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def write_h_regs(self, address: int, words_l: List[int], srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        """Call by server for writing in the holding registers space

        :param address: start address
        :type address: int
        :param words_l: list of word value to write
        :type words_l: list
        :param srv_info: some server info
        :type srv_info: ModbusServer.ServerInfo
        :rtype: Return
        """
        # write words to DataBank
        update_ok = self.data_bank.set_holding_registers(address, words_l, srv_info)
        # return DataStatus to server
        if update_ok:
            return DataHandler.Return(exp_code=EXP_NONE)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def read_i_regs(self, address: int, count: int, srv_info: ModbusServer.ServerInfo) -> DataHandler.Return:
        """Call by server for reading in the input registers space

        :param address: start address
        :type address: int
        :param count: number of input registers
        :type count: int
        :param srv_info: some server info
        :type srv_info: ModbusServer.ServerInfo
        :rtype: Return
        """
        # read words from DataBank
        words_l = self.data_bank.get_input_registers(address, count, srv_info)
        # return DataStatus to server
        if words_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)


class DeviceIdentification:
    """ Container class for device identification objects (MEI type 0x0E) return by function 0x2B. """

    def __init__(self, vendor_name: bytes = b'', product_code: bytes = b'', major_minor_revision: bytes = b'',
                 vendor_url: bytes = b'', product_name: bytes = b'', model_name: bytes = b'',
                 user_application_name: bytes = b'', objects_id: Optional[Dict[int, bytes]] = None) -> None:
        """
        Constructor

        :param vendor_name: VendorName mandatory object
        :type vendor_name: bytes
        :param product_code: ProductCode mandatory object
        :type product_code: bytes
        :param major_minor_revision: MajorMinorRevision mandatory object
        :type major_minor_revision: bytes
        :param vendor_url: VendorUrl regular object
        :type vendor_url: bytes
        :param product_name: ProductName regular object
        :type product_name: bytes
        :param model_name: ModelName regular object
        :type model_name: bytes
        :param user_application_name: UserApplicationName regular object
        :type user_application_name: bytes
        :param objects_id: Objects values by id as dict example: {42:b'value'} (optional)
        :type objects_id: dict
        """
        # private
        self._objs_d: Dict[int, bytes] = {}
        self._objs_lock = Lock()
        # default values
        self.vendor_name = vendor_name
        self.product_code = product_code
        self.major_minor_revision = major_minor_revision
        self.vendor_url = vendor_url
        self.product_name = product_name
        self.model_name = model_name
        self.user_application_name = user_application_name
        # process objects_id dict (populate no name objects)
        if isinstance(objects_id, dict):
            for key, value in objects_id.items():
                self[key] = value

    def __getitem__(self, key: int) -> bytes:
        if not isinstance(key, int):
            raise TypeError('key must be an int')
        with self._objs_lock:
            return self._objs_d[key]

    def __setitem__(self, key: int, value: bytes) -> None:
        if not isinstance(key, int):
            raise TypeError('key must be an int')
        if 0xff >= key >= 0x00:
            if not isinstance(value, bytes):
                raise TypeError('this object is of type bytes only')
            with self._objs_lock:
                self._objs_d[key] = value
        else:
            raise ValueError('key not in valid range (0 to 255)')

    def __repr__(self) -> str:
        named_params = ''
        # add named parameters
        for prop_name in ('vendor_name', 'product_code', 'major_minor_revision', 'vendor_url',
                          'product_name', 'model_name', 'user_application_name'):
            prop_value = getattr(self, prop_name)
            if prop_value:
                if named_params:
                    named_params += ', '
                named_params += '%s=%r' % (prop_name, getattr(self, prop_name))
        # add parameters without shortcut name
        objs_id_d_str = ''
        for _id in range(0x07, 0x100):
            try:
                obj_id_item = '%r: %r' % (_id, self[_id])
                if objs_id_d_str:
                    objs_id_d_str += ', '
                objs_id_d_str += obj_id_item
            except KeyError:
                pass
        # format str: classname(params_name=value, ..., objects_id={42: 'value'})
        class_args = named_params
        if objs_id_d_str:
            if class_args:
                class_args += ', '
            class_args += 'objects_id={%s}' % objs_id_d_str
        return '%s(%s)' % (self.__class__.__name__, class_args)

    @property
    def vendor_name(self) -> bytes:
        return self[0]

    @vendor_name.setter
    def vendor_name(self, value: bytes) -> None:
        self[0] = value

    @property
    def product_code(self) -> bytes:
        return self[1]

    @product_code.setter
    def product_code(self, value: bytes) -> None:
        self[1] = value

    @property
    def major_minor_revision(self) -> bytes:
        return self[2]

    @major_minor_revision.setter
    def major_minor_revision(self, value: bytes) -> None:
        self[2] = value

    @property
    def vendor_url(self) -> bytes:
        return self[3]

    @vendor_url.setter
    def vendor_url(self, value: bytes) -> None:
        self[3] = value

    @property
    def product_name(self) -> bytes:
        return self[4]

    @product_name.setter
    def product_name(self, value: bytes) -> None:
        self[4] = value

    @property
    def model_name(self) -> bytes:
        return self[5]

    @model_name.setter
    def model_name(self, value: bytes) -> None:
        self[5] = value

    @property
    def user_application_name(self) -> bytes:
        return self[6]

    @user_application_name.setter
    def user_application_name(self, value: bytes) -> None:
        self[6] = value

    def items(self, start: int = 0x00, end: int = 0xff) -> List[Tuple[int, bytes]]:
        items_l = []
        for obj_id in range(start, end + 1):
            try:
                items_l.append((obj_id, self[obj_id]))
            except KeyError:
                pass
        return items_l


class ModbusServer:
    """ Modbus TCP server """

    # interval (in s) at which the main server loop checks for a stop request: this is the max latency of stop()
    _SERVE_POLL_INTERVAL: float = 0.05

    class Error(Exception):
        """ Base exception for ModbusServer related errors. """
        pass

    class NetworkError(Error):
        """ Exception raise by ModbusServer on I/O errors. """
        pass

    class DataFormatError(Error):
        """ Exception raise by ModbusServer for data format errors. """
        pass

    class ClientInfo:
        """ Container class for client information """

        def __init__(self, address: str = '', port: int = 0) -> None:
            self.address = address
            self.port = port

        def __repr__(self) -> str:
            return 'ClientInfo(address=%r, port=%r)' % (self.address, self.port)

    class ServerInfo:
        """ Container class for server information """

        def __init__(self) -> None:
            self.client = ModbusServer.ClientInfo()
            self.recv_frame = ModbusServer.Frame()

    class SessionData:
        """ Container class for server session data. """

        def __init__(self) -> None:
            self.client = ModbusServer.ClientInfo()
            self.request = ModbusServer.Frame()
            self.response = ModbusServer.Frame()

        @property
        def srv_info(self) -> ModbusServer.ServerInfo:
            info = ModbusServer.ServerInfo()
            info.client = self.client
            info.recv_frame = self.request
            return info

        def new_request(self) -> None:
            self.request = ModbusServer.Frame()
            self.response = ModbusServer.Frame()

        def set_response_mbap(self) -> None:
            self.response.mbap.transaction_id = self.request.mbap.transaction_id
            self.response.mbap.protocol_id = self.request.mbap.protocol_id
            self.response.mbap.unit_id = self.request.mbap.unit_id

    class Frame:
        def __init__(self) -> None:
            """ Modbus Frame container. """
            self.mbap = ModbusServer.MBAP()
            self.pdu = ModbusServer.PDU()

        @property
        def raw(self) -> bytes:
            self.mbap.length = len(self.pdu) + 1
            return self.mbap.raw + self.pdu.raw

    class MBAP:
        """ MBAP (Modbus Application Protocol) container class. """

        def __init__(self, transaction_id: int = 0, protocol_id: int = 0, length: int = 0, unit_id: int = 0) -> None:
            # public
            self.transaction_id = transaction_id
            self.protocol_id = protocol_id
            self.length = length
            self.unit_id = unit_id

        @property
        def raw(self) -> bytes:
            try:
                return struct.pack('>HHHB', self.transaction_id,
                                   self.protocol_id, self.length,
                                   self.unit_id)
            except struct.error as e:
                raise ModbusServer.DataFormatError('MBAP raw encode pack error: %s' % e)

        @raw.setter
        def raw(self, value: bytes) -> None:
            # close connection if no standard 7 bytes mbap header
            if not (value and len(value) == 7):
                raise ModbusServer.DataFormatError('MBAP must have a length of 7 bytes')
            # decode header
            (self.transaction_id, self.protocol_id,
             self.length, self.unit_id) = struct.unpack('>HHHB', value)
            # check frame header content inconsistency
            if self.protocol_id != 0:
                raise ModbusServer.DataFormatError('MBAP protocol ID must be 0')
            if not 2 <= self.length <= 253:
                raise ModbusServer.DataFormatError('MBAP length must be between 2 and 253')

    class PDU:
        """ PDU (Protocol Data Unit) container class. """

        def __init__(self, raw: bytes = b'') -> None:
            """
            Constructor

            :param raw: raw PDU
            :type raw: bytes
            """
            self.raw = raw

        def __len__(self) -> int:
            return len(self.raw)

        @property
        def func_code(self) -> int:
            return self.raw[0]

        @property
        def except_code(self) -> int:
            return self.raw[1]

        @property
        def is_except(self) -> bool:
            return self.func_code > 0x7F

        @property
        def is_valid(self) -> bool:
            # PDU min length is 2 bytes
            return self.__len__() >= 2

        def clear(self) -> None:
            self.raw = b''

        def build_except(self, func_code: int, exp_status: int) -> ModbusServer.PDU:
            self.clear()
            self.add_pack('BB', func_code + 0x80, exp_status)
            return self

        def add_pack(self, fmt: str, *args: Any) -> None:
            try:
                self.raw += struct.pack(fmt, *args)
            except struct.error:
                err_msg = 'unable to format PDU message (fmt: %s, values: %s)' % (fmt, args)
                raise ModbusServer.DataFormatError(err_msg)

        def unpack(self, fmt: str, from_byte: Optional[int] = None, to_byte: Optional[int] = None) -> Tuple[Any, ...]:
            raw_section = self.raw[from_byte:to_byte]
            try:
                return struct.unpack(fmt, raw_section)
            except struct.error:
                err_msg = "unable to decode PDU message  (fmt: '%s', values: %r)" % (fmt, raw_section)
                raise ModbusServer.DataFormatError(err_msg)

    class CustomThreadingTCPServer(ThreadingTCPServer):
        """IPv4 threaded TCP server."""
        daemon_threads: bool = True
        # listen backlog: the socketserver default (5) makes a burst of simultaneous connections wait for a TCP SYN
        # retransmission (1 s or more) as soon as the accept loop is a bit late
        request_queue_size: int = 128
        # these 3 settings are set by ModbusServer.start() (see ModbusServer.__init__() for a description)
        max_connections: Optional[int] = None
        idle_timeout: Optional[float] = None
        request_timeout: Optional[float] = None
        evt_running: Event
        engine: Callable[[ModbusServer.SessionData], None]

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            # number of active sessions (a session = a client TCP connection with its thread)
            self._sessions = 0
            self._sessions_lock = Lock()
            # connections rejected since the last warning in the log (don't flood the log on a connection flood)
            self._rejected = 0
            self._rejected_log_time: Optional[float] = None
            super().__init__(*args, **kwargs)

        def verify_request(self, request: Any, client_address: Any) -> bool:
            # called by the main server thread, the only one that add sessions (so this check is safe)
            if self.max_connections is not None and self._sessions >= self.max_connections:
                self._rejected += 1
                # log the first rejection at once, then one message per 10 s at most
                now = time.monotonic()
                if self._rejected_log_time is None or now - self._rejected_log_time >= 10.0:
                    logger.warning('Maximum number of connections (%d) reached: %d connection(s) rejected '
                                   '(last from %r)', self.max_connections, self._rejected, client_address)
                    self._rejected = 0
                    self._rejected_log_time = now
                return False
            return True

        def process_request(self, request: Any, client_address: Any) -> None:
            with self._sessions_lock:
                self._sessions += 1
            try:
                super().process_request(request, client_address)
            except BaseException:
                # the session thread can't start: no session
                with self._sessions_lock:
                    self._sessions -= 1
                raise

        def process_request_thread(self, request: Any, client_address: Any) -> None:
            try:
                super().process_request_thread(request, client_address)
            finally:
                with self._sessions_lock:
                    self._sessions -= 1

    class CustomThreadingTCPServerV6(CustomThreadingTCPServer):
        """IPv6 threaded TCP server."""
        address_family = socket.AF_INET6

    class ModbusService(BaseRequestHandler):
        # default socket timeout (in s) on blocking operations
        _SOCKET_TIMEOUT: float = 1.0
        server: ModbusServer.CustomThreadingTCPServer

        @property
        def server_running(self) -> bool:
            return bool(self.server.evt_running.is_set())

        def _send_all(self, data: bytes) -> None:
            try:
                self.request.sendall(data)
            except socket.timeout:
                # sendall() may have sent only a part of the frame, the TCP stream is now out of sync:
                # raise an error to close this session (see handle())
                raise ModbusServer.NetworkError('timeout on send, close session')

        def _check_and_update_timeout(self, deadline: Optional[float], timeout_msg: str) -> bool:
            """Check the deadline (on a system call is only needed when it is near)"""
            sock_timeout_changed = False
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModbusServer.NetworkError(timeout_msg)
                if remaining < self._SOCKET_TIMEOUT:
                    self.request.settimeout(remaining)
                    sock_timeout_changed = True
            return sock_timeout_changed

        def _recv_chunk(self, size: int, data_len: int, deadline: Optional[float], timeout_msg: str,
                        request_timeout: Optional[float]) -> Tuple[bytes, Optional[float], str, bool]:
            # avoid keeping this TCP thread run after server.stop() on main server
            if not self.server_running:
                raise ModbusServer.NetworkError('main server is not running')

            sock_timeout_changed = self._check_and_update_timeout(deadline, timeout_msg)

            # recv all data or a chunk of it
            data_chunk = self.request.recv(size - data_len)

            # check data chunk
            if data_chunk:
                if data_len == 0 and request_timeout is not None:
                    # the request is started, the idle wait is over
                    deadline = time.monotonic() + request_timeout
                    timeout_msg = 'request timeout'
                return data_chunk, deadline, timeout_msg, sock_timeout_changed
            else:
                raise ModbusServer.NetworkError('recv return null')

        def _recv_all(self, size: int, deadline: Optional[float] = None, timeout_msg: str = 'recv timeout',
                      request_timeout: Optional[float] = None) -> bytes:
            """Receive size bytes (loop until all bytes are received).

            :param deadline: limit for the end of reception (a time.monotonic() value), None for no limit
            :param timeout_msg: message of the error raised if the deadline is reached
            :param request_timeout: if set, the deadline is replaced by "now + request_timeout" as soon as the first
                data is received (used to wait for a new request, without limit or with an idle deadline, then limit
                the time taken to receive it)
            :raises ModbusServer.NetworkError: on deadline, on connection closed or if the main server is stopped
            """
            data = b''
            sock_timeout_changed = False
            try:
                while len(data) < size:
                    try:
                        data_chunk, deadline, timeout_msg, timeout_changed = self._recv_chunk(
                            size, len(data), deadline, timeout_msg, request_timeout
                        )
                        if timeout_changed:
                            sock_timeout_changed = True
                        data += data_chunk
                    except socket.timeout:
                        # just redo main server run test, deadline test and recv operations on timeout
                        pass
            finally:
                # restore the default timeout used by the other socket operations
                if sock_timeout_changed:
                    self.request.settimeout(self._SOCKET_TIMEOUT)
            return data

        def setup(self) -> None:
            # set a socket timeout of 1s on blocking operations (like send/recv)
            # this avoids hang thread deletion when main server exit (see _recv_all method)
            self.request.settimeout(self._SOCKET_TIMEOUT)

        def handle(self) -> None:
            # try/except: end current thread on ModbusServer._InternalError, OSError or socket.error
            # this also close the current TCP session associated with it
            try:
                # init and update server info structure
                session_data = ModbusServer.SessionData()
                (session_data.client.address, session_data.client.port) = self.request.getpeername()[:2]
                # debug message
                logger.debug('Accept new connection from %r', session_data.client)
                # main processing loop
                while True:
                    # init session data for new request
                    session_data.new_request()
                    # receive mbap from client:
                    # - wait for a new request, the session is closed after idle_timeout without data (if set)
                    # - then the rest of the header must come in request_timeout (avoid a stalled request)
                    idle_timeout = self.server.idle_timeout
                    request_timeout = self.server.request_timeout
                    idle_deadline = None if idle_timeout is None else time.monotonic() + idle_timeout
                    session_data.request.mbap.raw = self._recv_all(7, idle_deadline, 'idle timeout', request_timeout)
                    # receive pdu from client (same limit)
                    req_deadline = None if request_timeout is None else time.monotonic() + request_timeout
                    session_data.request.pdu.raw = self._recv_all(session_data.request.mbap.length - 1, req_deadline,
                                                                  'request timeout')
                    # update response MBAP fields with request data
                    session_data.set_response_mbap()
                    # pass the current session data to request engine
                    self.server.engine(session_data)
                    # send the tx pdu with the last rx mbap (only length field change)
                    self._send_all(session_data.response.raw)
            except (ModbusServer.Error, OSError, socket.error) as e:
                # debug message
                logger.debug('Exception %r for %r', e, session_data.client)
                # on main loop except: exit from it and cleanly close the current socket
                self.request.close()

    def __init__(self, host: str = 'localhost', port: int = 502, no_block: bool = False, ipv6: bool = False,
                 data_bank: Optional[DataBank] = None, data_hdl: Optional[DataHandler] = None,
                 ext_engine: Optional[Callable[[ModbusServer.SessionData], None]] = None,
                 device_id: Optional[DeviceIdentification] = None,
                 request_timeout: Optional[float] = 30.0, idle_timeout: Optional[float] = None,
                 max_connections: Optional[int] = None, port_range: Optional[Tuple[int, int]] = None) -> None:
        """Initialize a Modbus TCP server.

        Sets up a Modbus TCP server with configurable network parameters, data handling,
        and optional device identification. Type validation is performed on critical
        parameters to prevent silent runtime failures.


        :param host: hostname or IPv4/IPv6 address server address (default is 'localhost')
        :type host: str
        :param port: TCP port number (default is 502), use 0 to let the OS choose a free port
        :type port: int
        :param no_block: no block mode, i.e. start() will return (default is False)
        :type no_block: bool
        :param ipv6: use ipv6 stack (default is False)
        :type ipv6: bool
        :param data_bank: instance of custom data bank, if you don't want the default one (optional)
        :type data_bank: DataBank
        :param data_hdl: instance of custom data handler, if you don't want the default one (optional)
        :type data_hdl: DataHandler
        :param ext_engine: an external engine reference (ref to ext_engine(session_data)) (optional)
        :type ext_engine: callable
        :param device_id: instance of DeviceIdentification class for read device identification request (optional)
        :type device_id: DeviceIdentification
        :param request_timeout: max time in seconds to receive a request once it has started (the MBAP header, then\
            the PDU: a stalled request), on expiry the client session is closed, None to disable (default is 30.0)
        :type request_timeout: float or None
        :param idle_timeout: max time in seconds without any request from a client before closing its session,\
            None to disable (default)
        :type idle_timeout: float or None
        :param max_connections: max number of simultaneous client connections, the next ones are closed immediately,\
            None for no limit (default)
        :type max_connections: int or None
        :param port_range: inclusive range (first, last) of TCP ports to try in order, the first free one is used,\
            when set the port param is ignored, None to disable (default)
        :type port_range: tuple(int, int) or None
        """
        # validate complex objects (data storage and callbacks)
        self._validate_data_bank_and_handler(data_bank, data_hdl)
        self._validate_ext_engine(ext_engine)
        device_id = self._validate_device_id(device_id)

        # validate numeric parameters with constraints
        request_timeout = self._check_timeout(request_timeout, 'request_timeout')
        idle_timeout = self._check_timeout(idle_timeout, 'idle_timeout')
        max_connections = self._validate_max_connections(max_connections)
        port_range = self._validate_port_range(port_range)

        # store network configuration
        self.host = host
        self.port = port
        self.port_range = port_range
        self.no_block = no_block
        self.ipv6 = ipv6

        # store timeout settings
        self.request_timeout = request_timeout
        self.idle_timeout = idle_timeout
        self.max_connections = max_connections

        # store callbacks and device info
        self.ext_engine = ext_engine
        self.device_id = device_id

        # initialize data storage (prefer data_hdl's internal bank, then explicit data_bank, then create new)
        if data_hdl:
            self.data_bank = data_hdl.data_bank
            self.data_hdl = data_hdl
        else:
            self.data_bank = data_bank or DataBank(virtual_mode=bool(ext_engine))
            self.data_hdl = DataHandler(data_bank=self.data_bank)

        # initialize internal state for server operation
        self._evt_running = Event()  # Event to signal server is running
        # active server instance
        self._service: Optional[Union[ModbusServer.CustomThreadingTCPServer,
                                      ModbusServer.CustomThreadingTCPServerV6]] = None
        # server thread handle
        self._serve_th: Optional[Thread] = None
        # (host, port) of the listening socket, set as soon as the server is bound (see bound_address)
        self._bound_address: Optional[Tuple[str, int]] = None

        # map Modbus function codes to handler methods (fixed routing table)
        self._func_map: Dict[int, Callable[[ModbusServer.SessionData], None]] = {
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
    def _validate_data_bank_and_handler(data_bank: Optional[DataBank], data_hdl: Optional[DataHandler]) -> None:
        if data_bank and not isinstance(data_bank, DataBank):
            raise TypeError('data_bank is not a DataBank instance')
        if data_hdl and not isinstance(data_hdl, DataHandler):
            raise TypeError('data_hdl is not a DataHandler instance')
        if data_hdl and data_bank:
            raise ValueError('when data_hdl is set, you must define data_bank in it')

    @staticmethod
    def _validate_ext_engine(ext_engine: Optional[Callable[[SessionData], None]]) -> None:
        if ext_engine and not callable(ext_engine):
            raise TypeError('ext_engine must be callable')

    @staticmethod
    def _validate_device_id(device_id: Optional[DeviceIdentification]) -> Optional[DeviceIdentification]:
        if device_id is None:
            return None
        if not isinstance(device_id, DeviceIdentification):
            raise TypeError(f'device_id must be a DeviceIdentification instance or None, '
                            f'got {type(device_id).__name__}')
        return device_id

    @staticmethod
    def _check_timeout(value: Optional[Union[int, float]], name: str) -> Optional[float]:
        """Check an optional timeout (None or a number of seconds > 0) and return it as a float (or None)."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError('%s must be a number or None' % name)
        if value <= 0:
            raise ValueError('%s must be greater than 0 (or None to disable it)' % name)
        return float(value)

    @staticmethod
    def _validate_max_connections(max_connections: Optional[int]) -> Optional[int]:
        if max_connections is None:
            return None
        if isinstance(max_connections, bool):
            raise TypeError('max_connections must be an int or None')
        if not isinstance(max_connections, int):
            raise TypeError('max_connections must be an int or None')
        if max_connections < 1:
            raise ValueError('max_connections must be at least 1 (or None for no limit)')
        return max_connections

    @staticmethod
    def _validate_port_range(port_range: Optional[Tuple[int, int]]) -> Optional[Tuple[int, int]]:
        if port_range is None:
            return None
        try:
            first, last = port_range
        except (TypeError, ValueError):
            raise TypeError('port_range must be a (first, last) tuple of int or None') from None
        for port in (first, last):
            if isinstance(port, bool) or not isinstance(port, int):
                raise TypeError('port_range must be a (first, last) tuple of int or None')
        if not (0 <= first <= last <= 0xffff):
            raise ValueError('port_range must verify 0 <= first <= last <= 65535')
        return (first, last)

    def __repr__(self) -> str:
        r_str = 'ModbusServer(host=\'%s\', port=%d, no_block=%s, ipv6=%s, data_bank=%s, data_hdl=%s, ext_engine=%s)'
        r_str %= (self.host, self.port, self.no_block, self.ipv6, self.data_bank, self.data_hdl, self.ext_engine)
        return r_str

    def _engine(self, session_data: ModbusServer.SessionData) -> None:
        """Main request processing engine.

        :type session_data: ModbusServer.SessionData
        """
        # call external engine or internal one (if ext_engine undefined)
        if callable(self.ext_engine):
            try:
                self.ext_engine(session_data)
            except Exception as e:
                raise ModbusServer.Error('external engine raise an exception: %r' % e)
        else:
            self._internal_engine(session_data)

    def _internal_engine(self, session_data: ModbusServer.SessionData) -> None:
        """Default internal processing engine: call default modbus func.

        :type session_data: ModbusServer.SessionData
        """
        func_code = session_data.request.pdu.func_code
        # get the ad-hoc function, if none exists (or is disabled with None), send an "illegal function" exception
        func = self._func_map.get(func_code)
        if not callable(func):
            session_data.response.pdu.build_except(func_code, EXP_ILLEGAL_FUNCTION)
            return
        # call ad-hoc func
        try:
            func(session_data)
        except ModbusServer.Error:
            # malformed frame or network error: let the session handler close the connection
            raise
        except Exception:
            # unexpected error (like an exception raised by a user callback): log it and keep the session alive
            # (this keeps the TCP session synchronized and tells the client that the request has failed)
            logger.exception('Unexpected error during processing of function 0x%02X', func_code)
            session_data.response.pdu.build_except(func_code, EXP_SLAVE_DEVICE_FAILURE)

    def _read_bits(self, session_data: ModbusServer.SessionData) -> None:
        """
        Functions Read Coils (0x01) or Read Discrete Inputs (0x02).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
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
            if ret_hdl.ok and ret_hdl.data is not None:
                # pack data bank bits in bytes
                bytes_b = _pack_bits(ret_hdl.data)
                # build pdu
                send_pdu.add_pack('BB%ds' % len(bytes_b), recv_pdu.func_code, len(bytes_b), bytes_b)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _read_words(self, session_data: ModbusServer.SessionData) -> None:
        """
        Functions Read Holding Registers (0x03) or Read Input Registers (0x04).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
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
            if ret_hdl.ok and ret_hdl.data is not None:
                # build pdu
                # (header + requested words in a single pack: avoid an intermediate bytes copy)
                send_pdu.add_pack('>BB%dH' % len(ret_hdl.data), recv_pdu.func_code, quantity_regs * 2, *ret_hdl.data)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_single_coil(self, session_data: ModbusServer.SessionData) -> None:
        """
        Function Write Single Coil (0x05).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
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

    def _write_single_register(self, session_data: ModbusServer.SessionData) -> None:
        """
        Functions Write Single Register (0x06).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
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

    def _write_multiple_coils(self, session_data: ModbusServer.SessionData) -> None:
        """
        Function Write Multiple Coils (0x0F).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (start_addr, quantity_bits, byte_count) = recv_pdu.unpack('>HHB', from_byte=1, to_byte=6)
        # ok flags: some tests on pdu fields
        qty_bits_ok = 0x0001 <= quantity_bits <= 0x07B0
        b_count_ok = byte_count >= (quantity_bits + 7) // 8
        pdu_len_ok = len(recv_pdu.raw[6:]) >= byte_count
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

    def _write_multiple_registers(self, session_data: ModbusServer.SessionData) -> None:
        """
        Function Write Multiple Registers (0x10).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (start_addr, quantity_regs, byte_count) = recv_pdu.unpack('>HHB', from_byte=1, to_byte=6)
        # ok flags: some tests on pdu fields
        qty_regs_ok = 0x0001 <= quantity_regs <= 0x007B
        b_count_ok = byte_count == quantity_regs * 2
        pdu_len_ok = len(recv_pdu.raw[6:]) >= byte_count
        # test ok flags
        if qty_regs_ok and b_count_ok and pdu_len_ok:
            # words list from rx frame
            regs_l = list(recv_pdu.unpack('>%dH' % quantity_regs, from_byte=6, to_byte=6 + quantity_regs * 2))
            # data handler update request
            ret_hdl = self.data_hdl.write_h_regs(start_addr, regs_l, session_data.srv_info)
            # format regular or except response
            if ret_hdl.ok:
                send_pdu.add_pack('>BHH', recv_pdu.func_code, start_addr, quantity_regs)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_read_multiple_registers(self, session_data: ModbusServer.SessionData) -> None:
        """
        Function Write Read Multiple Registers (0x17).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
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
        write_qty_regs_ok = 0x0001 <= write_quantity_regs <= 0x0079
        write_b_count_ok = byte_count == write_quantity_regs * 2
        write_pdu_len_ok = len(recv_pdu.raw[10:]) >= byte_count
        read_qty_regs_ok = 0x0001 <= read_quantity_regs <= 0x007D
        # test ok flags
        if not (write_qty_regs_ok and write_b_count_ok and write_pdu_len_ok and read_qty_regs_ok):
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)
            return
        # words list from rx frame
        regs_l = list(recv_pdu.unpack('>%dH' % write_quantity_regs,
                                      from_byte=10, to_byte=10 + write_quantity_regs * 2))
        # gratuitous read to check error status (avoid to write if read fail)
        ret_hdl_read = self.data_hdl.read_h_regs(read_start_addr, read_quantity_regs, session_data.srv_info)
        if not ret_hdl_read.ok or ret_hdl_read.data is None:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl_read.exp_code)
            return
        # do write
        ret_hdl_write = self.data_hdl.write_h_regs(write_start_addr, regs_l, session_data.srv_info)
        if not ret_hdl_write.ok:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl_write.exp_code)
            return
        # redo read after write
        ret_hdl_read = self.data_hdl.read_h_regs(read_start_addr, read_quantity_regs, session_data.srv_info)
        if not ret_hdl_read.ok or ret_hdl_read.data is None:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl_read.exp_code)
            return
        # build pdu
        send_pdu.add_pack('BB', recv_pdu.func_code, read_quantity_regs * 2)
        # add_pack requested words
        send_pdu.add_pack('>%dH' % len(ret_hdl_read.data), *ret_hdl_read.data)

    def _encapsulated_interface_transport(self, session_data: ModbusServer.SessionData) -> None:
        """
        Modbus Encapsulated Interface transport (MEI) endpoint (0x2B).

        :param session_data: server engine data
        :type session_data: ModbusServer.SessionData
        """
        # pdu alias
        recv_pdu = session_data.request.pdu
        send_pdu = session_data.response.pdu
        # decode pdu
        (mei_type,) = recv_pdu.unpack('B', from_byte=1, to_byte=2)
        # MEI type: read device identification
        if mei_type == MEI_TYPE_READ_DEVICE_ID:
            # check device_id property is set (default is None)
            if not self.device_id:
                # return except 2 if unset
                send_pdu.build_except(recv_pdu.func_code, EXP_DATA_ADDRESS)
                return
            # list of requested objects
            req_objects_l: List[Tuple[int, bytes]] = list()
            (device_id_code, object_id) = recv_pdu.unpack('BB', from_byte=2, to_byte=4)
            # get basic device id (object id from 0x00 to 0x02)
            if device_id_code == 1:
                start_id = object_id
                req_objects_l.extend(self.device_id.items(start=start_id, end=0x2))
            # get regular device id (object id 0x03 to 0x7f)
            elif device_id_code == 2:
                start_id = max(object_id, 0x03)
                req_objects_l.extend(self.device_id.items(start=start_id, end=0x7f))
            # get extended device id (object id 0x80 to 0xff)
            elif device_id_code == 3:
                start_id = max(object_id, 0x80)
                req_objects_l.extend(self.device_id.items(start=start_id, end=0xff))
            # get specific id object
            elif device_id_code == 4:
                start_id = object_id
                req_objects_l.extend(self.device_id.items(start=start_id, end=start_id))
            else:
                # return except 3 for unknown device id code
                send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)
                return
            # init variables for response PDU build
            conformity_level = 0x83
            more_follow = 0
            next_obj_id = 0
            number_of_objs = 0
            fmt_pdu_head = 'BBBBBBB'
            # format objects data part = [[obj id, obj len, obj val], ...]
            obj_data_part = b''
            for req_obj_id, req_obj_value in req_objects_l:
                fmt_obj_blk = 'BB%ds' % len(req_obj_value)
                # skip if the next add to data part will exceed max PDU size of modbus frame
                if struct.calcsize(fmt_pdu_head) + len(obj_data_part) + struct.calcsize(fmt_obj_blk) > MAX_PDU_SIZE:
                    # turn on "more follow" field and set "next object id" field with next object id to ask
                    more_follow = 0xff
                    next_obj_id = req_obj_id
                    break
                # ensure bytes type for object value
                if isinstance(req_obj_value, str):
                    req_obj_value = req_obj_value.encode()
                # add current object to data part
                obj_data_part += struct.pack(fmt_obj_blk, req_obj_id, len(req_obj_value), req_obj_value)
                number_of_objs += 1
            # full PDU response = [PDU header] + [objects data part]
            send_pdu.add_pack(fmt_pdu_head, recv_pdu.func_code, mei_type, device_id_code,
                              conformity_level, more_follow, next_obj_id, number_of_objs)
            send_pdu.raw += obj_data_part
        else:
            # return except 2 for an unknown MEI type
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_ADDRESS)

    def _candidate_ports(self) -> range:
        """Ports to try at start: the whole port_range if set, otherwise just self.port."""
        if self.port_range is not None:
            return range(self.port_range[0], self.port_range[1] + 1)
        return range(self.port, self.port + 1)

    def _bind_service(self, port: int) -> None:
        """Create the listening server (self._service) on a given port.

        :raises ModbusServer.NetworkError: if the port is unavailable
        """
        # init server (IPv4 or IPv6)
        # here we subclass ThreadingTCPServer to don't alter the socketserver classes shared with other code
        server_cls = ModbusServer.CustomThreadingTCPServerV6 if self.ipv6 else ModbusServer.CustomThreadingTCPServer
        service = server_cls((self.host, port), self.ModbusService, bind_and_activate=False)
        # keep a reference on it, even if the bind below fails (the failed service is closed, as before)
        self._service = service
        # pass some things shared with server threads (access via self.server in ModbusService.handle())
        service.evt_running = self._evt_running
        service.engine = self._engine
        service.request_timeout = self.request_timeout
        service.idle_timeout = self.idle_timeout
        service.max_connections = self.max_connections
        # set socket options
        # In auto-port mode (port_range or port 0), we need a reliable "port already in use" detection. On Windows,
        # SO_REUSEADDR allows to bind a port already listening, so use SO_EXCLUSIVEADDRUSE there instead.
        auto_port = self.port_range is not None or port == 0
        if auto_port and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            service.socket.setsockopt(socket.SOL_SOCKET,
                                      socket.SO_EXCLUSIVEADDRUSE, 1)  # pyright: ignore[reportAttributeAccessIssue]
        else:
            service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        # TODO test no_delay with bench
        service.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # bind and activate
        try:
            service.server_bind()
            service.server_activate()
        except OSError as e:
            # don't keep the listening socket open after a failed start
            service.server_close()
            raise ModbusServer.NetworkError(e) from e
        # publish the address actually used (the port is allocated by the OS if port is 0)
        address = service.server_address
        self._bound_address = (str(address[0]), int(address[1]))
        logger.info('Listening on %s:%d', *self._bound_address)

    @staticmethod
    def _is_port_unavailable(error: ModbusServer.NetworkError) -> bool:
        """True if a bind error only means "try another port": the port is already in use or not allowed.

        Any other error (invalid host address, IP family not supported...) has nothing to do with the port:
        there is no point in trying the next one.
        """
        # EACCES: privileged port (< 1024) for a non-root user, WSAEACCES: port in use with SO_EXCLUSIVEADDRUSE (Windows)
        unavailable = {errno.EADDRINUSE, errno.EACCES}
        if os.name == 'nt':
            unavailable.add(getattr(errno, 'WSAEACCES', 10013))
        return getattr(error.__cause__, 'errno', None) in unavailable

    def start(self) -> Tuple[str, int]:
        """Start the server.

        This function will block (or not if no_block flag is set).

        If port_range is set (or port is 0), the first available TCP port is used.
        The full server address structure is returned.

        :return: A tuple containing the address information (e.g., ('127.0.0.1', 54321)).
        :raises ModbusServer.NetworkError: if the server can't listen (no port available)
        """
        # do nothing if server is already running
        if self.is_run:
            return self._bound_address if self._bound_address else ('', 0)
        # bind on the first available port
        last_error: Optional[ModbusServer.NetworkError] = None
        for port in self._candidate_ports():
            try:
                self._bind_service(port)
                break
            except ModbusServer.NetworkError as e:
                # an error not related to the port (bad host...) is the same for all ports: raise it at once
                if not self._is_port_unavailable(e):
                    raise
                last_error = e
        else:
            raise ModbusServer.NetworkError(last_error) from last_error

        # serve request
        if self.no_block:
            self._serve_th = Thread(target=self._serve, daemon=True)
            self._serve_th.start()
        else:
            self._serve()

        return self._bound_address if self._bound_address else ('', 0)

    def wait(self, timeout: Optional[float] = None) -> None:
        """Wait for the server thread to finish (useful in non-blocking mode)."""
        if self._serve_th is not None and self._serve_th.is_alive():
            self._serve_th.join(timeout=timeout)

    def stop(self) -> None:
        """Stop the server."""
        if self.is_run and self._service is not None:
            self._service.shutdown()
            self._service.server_close()
        self._bound_address = None

    @property
    def bound_address(self) -> Optional[Tuple[str, int]]:
        """The (host, port) the server listens on, None if it isn't started.

        Set as soon as the server is bound, so it is available from another thread even in blocking mode
        (no_block=False). This is the way to get the port allocated by the OS when port is 0 (or port_range is set).
        """
        return self._bound_address

    @property
    def bound_port(self) -> Optional[int]:
        """The TCP port the server listens on, None if it isn't started (see bound_address)."""
        return None if self._bound_address is None else self._bound_address[1]

    @property
    def is_run(self) -> bool:
        """Return True if server running."""
        return self._evt_running.is_set()

    def _serve(self) -> None:
        if self._service is None:
            return
        try:
            self._evt_running.set()
            self._service.serve_forever(poll_interval=self._SERVE_POLL_INTERVAL)
        except Exception:
            self._service.server_close()
            raise
        except KeyboardInterrupt:
            self._service.server_close()
        finally:
            self._evt_running.clear()
            self._bound_address = None
