""" pyModbusTCP Server """

from .constants import READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS, \
    WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS, WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, \
    EXP_NONE, EXP_ILLEGAL_FUNCTION, EXP_DATA_ADDRESS, EXP_DATA_VALUE, WRITE_READ_MULTIPLE_REGISTERS, \
    ENCAPSULATED_INTERFACE_TRANSPORT, MEI_TYPE_READ_DEVICE_ID, MAX_PDU_SIZE
from .utils import test_bit, set_bit
import logging
import socket
from socketserver import BaseRequestHandler, ThreadingTCPServer
import struct
from threading import Lock, Thread, Event
from warnings import warn

# add a logger for pyModbusTCP.server
logger = logging.getLogger('pyModbusTCP.server')


class DataBank:
    """ Data space class with thread safe access functions """

    _DEPR_MSG = 'This class method is deprecated. Use DataBank instance method instead: '

    @classmethod
    def get_bits(cls, *_args, **_kwargs):
        msg = DataBank._DEPR_MSG + 'server.data_bank.get_coils() or get_discrete_inputs()'
        warn(msg, DeprecationWarning, stacklevel=2)

    @classmethod
    def set_bits(cls, *_args, **_kwargs):
        msg = DataBank._DEPR_MSG + 'server.data_bank.set_coils() or set_discrete_inputs()'
        warn(msg, DeprecationWarning, stacklevel=2)

    @classmethod
    def get_words(cls, *_args, **_kwargs):
        msg = DataBank._DEPR_MSG + 'server.data_bank.get_holding_registers() or get_input_registers()'
        warn(msg, DeprecationWarning, stacklevel=2)

    @classmethod
    def set_words(cls, *_args, **_kwargs):
        msg = DataBank._DEPR_MSG + 'server.data_bank.set_holding_registers() or set_input_registers()'
        warn(msg, DeprecationWarning, stacklevel=2)

    def __init__(self, coils_size=0x10000, coils_default_value=False,
                 d_inputs_size=0x10000, d_inputs_default_value=False,
                 h_regs_size=0x10000, h_regs_default_value=0,
                 i_regs_size=0x10000, i_regs_default_value=0,
                 virtual_mode=False):
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

    def __repr__(self):
        attrs_str = ''
        for attr_name in self.__dict__:
            if isinstance(attr_name, str) and not attr_name.startswith('_'):
                if attrs_str:
                    attrs_str += ', '
                attrs_str += '%s=%r' % (attr_name, self.__dict__[attr_name])
        return 'DataBank(%s)' % attrs_str

    def get_coils(self, address, number=1, srv_info=None):
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

    def set_coils(self, address, bit_list, srv_info=None):
        """Write data to server coils space

        :param address: start address
        :type address: int
        :param bit_list: a list of bool to write
        :type bit_list: list
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServerInfo
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if bit_list members cannot be converted to bool
        """
        # ensure bit_list values are bool
        bit_list = [bool(b) for b in bit_list]
        # keep trace of any changes
        changes_list = []
        # ensure atomic update of internal data
        with self._coils_lock:
            if (address >= 0) and (address + len(bit_list) <= len(self._coils)):
                for offset, c_value in enumerate(bit_list):
                    c_address = address + offset
                    if self._coils[c_address] != c_value:
                        changes_list.append((c_address, self._coils[c_address], c_value))
                        self._coils[c_address] = c_value
            else:
                return None
        # on server update
        if srv_info:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_coils_change(address, from_value, to_value, srv_info)
        return True

    def get_discrete_inputs(self, address, number=1, srv_info=None):
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
        with self._d_inputs_lock:
            if (address >= 0) and (address + number <= len(self._coils)):
                return self._d_inputs[address: number + address]
            else:
                return None

    def set_discrete_inputs(self, address, bit_list):
        """Write data to server discrete inputs space

        :param address: start address
        :type address: int
        :param bit_list: a list of bool to write
        :type bit_list: list
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if bit_list members cannot be converted to bool
        """
        # ensure bit_list values are bool
        bit_list = [bool(b) for b in bit_list]
        # ensure atomic update of internal data
        with self._d_inputs_lock:
            if (address >= 0) and (address + len(bit_list) <= len(self._coils)):
                for offset, b_value in enumerate(bit_list):
                    self._d_inputs[address + offset] = b_value
            else:
                return None
        return True

    def get_holding_registers(self, address, number=1, srv_info=None):
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

    def set_holding_registers(self, address, word_list, srv_info=None):
        """Write data to server holding registers space

        :param address: start address
        :type address: int
        :param word_list: a list of word to write
        :type word_list: list
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ModbusServerInfo
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if word_list members cannot be converted to int
        """
        # ensure word_list values are int with a max bit length of 16
        word_list = [int(w) & 0xffff for w in word_list]
        # keep trace of any changes
        changes_list = []
        # ensure atomic update of internal data
        with self._h_regs_lock:
            if (address >= 0) and (address + len(word_list) <= len(self._h_regs)):
                for offset, c_value in enumerate(word_list):
                    c_address = address + offset
                    if self._h_regs[c_address] != c_value:
                        changes_list.append((c_address, self._h_regs[c_address], c_value))
                        self._h_regs[c_address] = c_value
            else:
                return None
        # on server update
        if srv_info:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_holding_registers_change(address, from_value, to_value, srv_info=srv_info)
        return True

    def get_input_registers(self, address, number=1, srv_info=None):
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

    def set_input_registers(self, address, word_list):
        """Write data to server input registers space

        :param address: start address
        :type address: int
        :param word_list: a list of word to write
        :type word_list: list
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if word_list members cannot be converted to int
        """
        # ensure word_list values are int with a max bit length of 16
        word_list = [int(w) & 0xffff for w in word_list]
        # ensure atomic update of internal data
        with self._i_regs_lock:
            if (address >= 0) and (address + len(word_list) <= len(self._i_regs)):
                for offset, c_value in enumerate(word_list):
                    c_address = address + offset
                    if self._i_regs[c_address] != c_value:
                        self._i_regs[c_address] = c_value
            else:
                return None
        return True

    def on_coils_change(self, address, from_value, to_value, srv_info):
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

    def on_holding_registers_change(self, address, from_value, to_value, srv_info):
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
        def __init__(self, exp_code, data=None):
            self.exp_code = exp_code
            self.data = data

        @property
        def ok(self):
            return self.exp_code == EXP_NONE

    def __init__(self, data_bank=None):
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

    def __repr__(self):
        return 'ModbusServerDataHandler(data_bank=%s)' % self.data_bank

    def read_coils(self, address, count, srv_info):
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

    def write_coils(self, address, bits_l, srv_info):
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

    def read_d_inputs(self, address, count, srv_info):
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

    def read_h_regs(self, address, count, srv_info):
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

    def write_h_regs(self, address, words_l, srv_info):
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

    def read_i_regs(self, address, count, srv_info):
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

    def __init__(self, vendor_name=b'', product_code=b'', major_minor_revision=b'', vendor_url=b'',
                 product_name=b'', model_name=b'', user_application_name=b'', objects_id=None):
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
        self._objs_d = {}
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

    def __getitem__(self, key):
        if not isinstance(key, int):
            raise TypeError('key must be an int')
        with self._objs_lock:
            return self._objs_d[key]

    def __setitem__(self, key, value):
        if not isinstance(key, int):
            raise TypeError('key must be an int')
        if 0xff >= key >= 0x00:
            if not isinstance(value, bytes):
                raise TypeError('this object is of type bytes only')
            with self._objs_lock:
                self._objs_d[key] = value
        else:
            raise ValueError('key not in valid range (0 to 255)')

    def __repr__(self):
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
    def vendor_name(self):
        return self[0]

    @vendor_name.setter
    def vendor_name(self, value):
        self[0] = value

    @property
    def product_code(self):
        return self[1]

    @product_code.setter
    def product_code(self, value):
        self[1] = value

    @property
    def major_minor_revision(self):
        return self[2]

    @major_minor_revision.setter
    def major_minor_revision(self, value):
        self[2] = value

    @property
    def vendor_url(self):
        return self[3]

    @vendor_url.setter
    def vendor_url(self, value):
        self[3] = value

    @property
    def product_name(self):
        return self[4]

    @product_name.setter
    def product_name(self, value):
        self[4] = value

    @property
    def model_name(self):
        return self[5]

    @model_name.setter
    def model_name(self, value):
        self[5] = value

    @property
    def user_application_name(self):
        return self[6]

    @user_application_name.setter
    def user_application_name(self, value):
        self[6] = value

    def items(self, start=0x00, end=0xff):
        items_l = []
        for obj_id in range(start, end + 1):
            try:
                items_l.append((obj_id, self[obj_id]))
            except KeyError:
                pass
        return items_l


class ModbusServer:
    """ Modbus TCP server """

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

        def __init__(self, address='', port=0):
            self.address = address
            self.port = port

        def __repr__(self):
            return 'ClientInfo(address=%r, port=%r)' % (self.address, self.port)

    class ServerInfo:
        """ Container class for server information """

        def __init__(self):
            self.client = ModbusServer.ClientInfo()
            self.recv_frame = ModbusServer.Frame()

    class SessionData:
        """ Container class for server session data. """

        def __init__(self):
            self.client = ModbusServer.ClientInfo()
            self.request = ModbusServer.Frame()
            self.response = ModbusServer.Frame()

        @property
        def srv_info(self):
            info = ModbusServer.ServerInfo()
            info.client = self.client
            info.recv_frame = self.request
            return info

        def new_request(self):
            self.request = ModbusServer.Frame()
            self.response = ModbusServer.Frame()

        def set_response_mbap(self):
            self.response.mbap.transaction_id = self.request.mbap.transaction_id
            self.response.mbap.protocol_id = self.request.mbap.protocol_id
            self.response.mbap.unit_id = self.request.mbap.unit_id

    class Frame:
        def __init__(self):
            """ Modbus Frame container. """
            self.mbap = ModbusServer.MBAP()
            self.pdu = ModbusServer.PDU()

        @property
        def raw(self):
            self.mbap.length = len(self.pdu) + 1
            return self.mbap.raw + self.pdu.raw

    class MBAP:
        """ MBAP (Modbus Application Protocol) container class. """

        def __init__(self, transaction_id=0, protocol_id=0, length=0, unit_id=0):
            # public
            self.transaction_id = transaction_id
            self.protocol_id = protocol_id
            self.length = length
            self.unit_id = unit_id

        @property
        def raw(self):
            try:
                return struct.pack('>HHHB', self.transaction_id,
                                   self.protocol_id, self.length,
                                   self.unit_id)
            except struct.error as e:
                raise ModbusServer.DataFormatError('MBAP raw encode pack error: %s' % e)

        @raw.setter
        def raw(self, value):
            # close connection if no standard 7 bytes mbap header
            if not (value and len(value) == 7):
                raise ModbusServer.DataFormatError('MBAP must have a length of 7 bytes')
            # decode header
            (self.transaction_id, self.protocol_id,
             self.length, self.unit_id) = struct.unpack('>HHHB', value)
            # check frame header content inconsistency
            if self.protocol_id != 0:
                raise ModbusServer.DataFormatError('MBAP protocol ID must be 0')
            if not 2 < self.length < 256:
                raise ModbusServer.DataFormatError('MBAP length must be between 2 and 256')

    class PDU:
        """ PDU (Protocol Data Unit) container class. """

        def __init__(self, raw=b''):
            """
            Constructor

            :param raw: raw PDU
            :type raw: bytes
            """
            self.raw = raw

        def __len__(self):
            return len(self.raw)

        @property
        def func_code(self):
            return self.raw[0]

        @property
        def except_code(self):
            return self.raw[1]

        @property
        def is_except(self):
            return self.func_code > 0x7F

        @property
        def is_valid(self):
            # PDU min length is 2 bytes
            return self.__len__() < 2

        def clear(self):
            self.raw = b''

        def build_except(self, func_code, exp_status):
            self.clear()
            self.add_pack('BB', func_code + 0x80, exp_status)
            return self

        def add_pack(self, fmt, *args):
            try:
                self.raw += struct.pack(fmt, *args)
            except struct.error:
                err_msg = 'unable to format PDU message (fmt: %s, values: %s)' % (fmt, args)
                raise ModbusServer.DataFormatError(err_msg)

        def unpack(self, fmt, from_byte=None, to_byte=None):
            raw_section = self.raw[from_byte:to_byte]
            try:
                return struct.unpack(fmt, raw_section)
            except struct.error:
                err_msg = 'unable to decode PDU message  (fmt: %s, values: %s)' % (fmt, raw_section)
                raise ModbusServer.DataFormatError(err_msg)

    class ModbusService(BaseRequestHandler):

        @property
        def server_running(self):
            return self.server.evt_running.is_set()

        def _send_all(self, data):
            try:
                self.request.sendall(data)
                return True
            except socket.timeout:
                return False

        def _recv_all(self, size):
            data = b''
            while len(data) < size:
                try:
                    # avoid keeping this TCP thread run after server.stop() on main server
                    if not self.server_running:
                        raise ModbusServer.NetworkError('main server is not running')
                    # recv all data or a chunk of it
                    data_chunk = self.request.recv(size - len(data))
                    # check data chunk
                    if data_chunk:
                        data += data_chunk
                    else:
                        raise ModbusServer.NetworkError('recv return null')
                except socket.timeout:
                    # just redo main server run test and recv operations on timeout
                    pass
            return data

        def setup(self):
            # set a socket timeout of 1s on blocking operations (like send/recv)
            # this avoids hang thread deletion when main server exit (see _recv_all method)
            self.request.settimeout(1.0)

        def handle(self):
            # try/except end current thread on ModbusServer._InternalError or socket.error
            # this also close the current TCP session associated with it
            # init and update server info structure
            session_data = ModbusServer.SessionData()
            (session_data.client.address, session_data.client.port) = self.request.getpeername()
            # debug message
            logger.debug('Accept new connection from %r', session_data.client)
            try:
                # main processing loop
                while True:
                    # init session data for new request
                    session_data.new_request()
                    # receive mbap from client
                    session_data.request.mbap.raw = self._recv_all(7)
                    # receive pdu from client
                    session_data.request.pdu.raw = self._recv_all(session_data.request.mbap.length - 1)
                    # update response MBAP fields with request data
                    session_data.set_response_mbap()
                    # pass the current session data to request engine
                    self.server.engine(session_data)
                    # send the tx pdu with the last rx mbap (only length field change)
                    self._send_all(session_data.response.raw)
            except (ModbusServer.Error, socket.error) as e:
                # debug message
                logger.debug('Exception during request handling: %r', e)
                # on main loop except: exit from it and cleanly close the current socket
                self.request.close()

    def __init__(self, host='localhost', port=502, no_block=False, ipv6=False,
                 data_bank=None, data_hdl=None, ext_engine=None, device_id=None):
        """Constructor

        Modbus server constructor.

        :param host: hostname or IPv4/IPv6 address server address (default is 'localhost')
        :type host: str
        :param port: TCP port number (default is 502)
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
        """
        # check data_bank
        if data_bank and not isinstance(data_bank, DataBank):
            raise TypeError('data_bank is not a DataBank instance')
        # check data_hdl
        if data_hdl and not isinstance(data_hdl, DataHandler):
            raise TypeError('data_hdl is not a DataHandler instance')
        # data_hdl and data_bank can't be set at same time
        if data_hdl and data_bank:
            raise ValueError('when data_hdl is set, you must define data_bank in it')
        # check ext_engine
        if ext_engine and not callable(ext_engine):
            raise TypeError('ext_engine must be callable')
        # check device_id
        if device_id and not isinstance(device_id, DeviceIdentification):
            raise TypeError('device_id is not a DeviceIdentification instance')
        # public
        self.host = host
        self.port = port
        self.no_block = no_block
        self.ipv6 = ipv6
        self.ext_engine = ext_engine
        # First, internal data_bank will be linked to an external data handler if defined.
        # If not, an external or internal DataBank will be used instead.
        # "virtual mode" will be set for save memory if an external engine is in use.
        self.data_bank = data_hdl.data_bank if data_hdl else data_bank or DataBank(virtual_mode=bool(ext_engine))
        self.data_hdl = data_hdl or DataHandler(data_bank=self.data_bank)
        self.device_id = device_id
        # private
        self._evt_running = Event()
        self._service = None
        self._serve_th = None
        # modbus default functions map
        self._func_map = {READ_COILS: self._read_bits,
                          READ_DISCRETE_INPUTS: self._read_bits,
                          READ_HOLDING_REGISTERS: self._read_words,
                          READ_INPUT_REGISTERS: self._read_words,
                          WRITE_SINGLE_COIL: self._write_single_coil,
                          WRITE_SINGLE_REGISTER: self._write_single_register,
                          WRITE_MULTIPLE_COILS: self._write_multiple_coils,
                          WRITE_MULTIPLE_REGISTERS: self._write_multiple_registers,
                          WRITE_READ_MULTIPLE_REGISTERS: self._write_read_multiple_registers,
                          ENCAPSULATED_INTERFACE_TRANSPORT: self._encapsulated_interface_transport}

    def __repr__(self):
        r_str = 'ModbusServer(host=\'%s\', port=%d, no_block=%s, ipv6=%s, data_bank=%s, data_hdl=%s, ext_engine=%s)'
        r_str %= (self.host, self.port, self.no_block, self.ipv6, self.data_bank, self.data_hdl, self.ext_engine)
        return r_str

    def _engine(self, session_data):
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

    def _internal_engine(self, session_data):
        """Default internal processing engine: call default modbus func.

        :type session_data: ModbusServer.SessionData
        """
        try:
            # call the ad-hoc function, if none exists, send an "illegal function" exception
            func = self._func_map[session_data.request.pdu.func_code]
            # check function found is callable
            if not callable(func):
                raise TypeError
            # call ad-hoc func
            func(session_data)
        except (TypeError, KeyError):
            session_data.response.pdu.build_except(session_data.request.pdu.func_code, EXP_ILLEGAL_FUNCTION)

    def _read_bits(self, session_data):
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
            if ret_hdl.ok:
                # allocate bytes list
                b_size = (quantity_bits + 7) // 8
                bytes_l = [0] * b_size
                # populate bytes list with data bank bits
                for i, item in enumerate(ret_hdl.data):
                    if item:
                        bytes_l[i // 8] = set_bit(bytes_l[i // 8], i % 8)
                # build pdu
                send_pdu.add_pack('BB', recv_pdu.func_code, len(bytes_l))
                send_pdu.add_pack('%dB' % len(bytes_l), *bytes_l)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _read_words(self, session_data):
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
            if ret_hdl.ok:
                # build pdu
                send_pdu.add_pack('BB', recv_pdu.func_code, quantity_regs * 2)
                # add_pack requested words
                send_pdu.add_pack('>%dH' % len(ret_hdl.data), *ret_hdl.data)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_single_coil(self, session_data):
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
        # format coil raw value to bool
        coil_as_bool = bool(coil_value == 0xFF00)
        # data handler update request
        ret_hdl = self.data_hdl.write_coils(coil_addr, [coil_as_bool], session_data.srv_info)
        # format regular or except response
        if ret_hdl.ok:
            send_pdu.add_pack('>BHH', recv_pdu.func_code, coil_addr, coil_value)
        else:
            send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)

    def _write_single_register(self, session_data):
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

    def _write_multiple_coils(self, session_data):
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
            # allocate bits list
            bits_l = [False] * quantity_bits
            # populate bits list with bits from rx frame
            for i, _ in enumerate(bits_l):
                bit_val = recv_pdu.raw[i // 8 + 6]
                bits_l[i] = test_bit(bit_val, i % 8)
            # data handler update request
            ret_hdl = self.data_hdl.write_coils(start_addr, bits_l, session_data.srv_info)
            # format regular or except response
            if ret_hdl.ok:
                send_pdu.add_pack('>BHH', recv_pdu.func_code, start_addr, quantity_bits)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_multiple_registers(self, session_data):
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
            # allocate words list
            regs_l = [0] * quantity_regs
            # populate words list with words from rx frame
            for i, _ in enumerate(regs_l):
                offset = i * 2 + 6
                regs_l[i] = recv_pdu.unpack('>H', from_byte=offset, to_byte=offset + 2)[0]
            # data handler update request
            ret_hdl = self.data_hdl.write_h_regs(start_addr, regs_l, session_data.srv_info)
            # format regular or except response
            if ret_hdl.ok:
                send_pdu.add_pack('>BHH', recv_pdu.func_code, start_addr, quantity_regs)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _write_read_multiple_registers(self, session_data):
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
        write_qty_regs_ok = 0x0001 <= write_quantity_regs <= 0x007B
        write_b_count_ok = byte_count == write_quantity_regs * 2
        write_pdu_len_ok = len(recv_pdu.raw[10:]) >= byte_count
        read_qty_regs_ok = 0x0001 <= read_quantity_regs <= 0x007B
        # test ok flags
        if write_qty_regs_ok and write_b_count_ok and write_pdu_len_ok and read_qty_regs_ok:
            # allocate words list
            regs_l = [0] * write_quantity_regs
            # populate words list with words from rx frame
            for i, _ in enumerate(regs_l):
                offset = i * 2 + 10
                regs_l[i] = recv_pdu.unpack('>H', from_byte=offset, to_byte=offset + 2)[0]
            # data handler update request
            ret_hdl = self.data_hdl.write_h_regs(write_start_addr, regs_l, session_data.srv_info)
            # format regular or except response
            if ret_hdl.ok:
                ret_hdl = self.data_hdl.read_h_regs(read_start_addr, read_quantity_regs, session_data.srv_info)
                if ret_hdl.ok:
                    # build pdu
                    send_pdu.add_pack('BB', recv_pdu.func_code, read_quantity_regs * 2)
                    # add_pack requested words
                    send_pdu.add_pack('>%dH' % len(ret_hdl.data), *ret_hdl.data)
                else:
                    send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
            else:
                send_pdu.build_except(recv_pdu.func_code, ret_hdl.exp_code)
        else:
            send_pdu.build_except(recv_pdu.func_code, EXP_DATA_VALUE)

    def _encapsulated_interface_transport(self, session_data):
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
            req_objects_l = list()
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
                fmt_obj_blk = 'BB%ss' % len(req_obj_value)
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

    def start(self):
        """Start the server.

        This function will block (or not if no_block flag is set).
        """
        # do nothing if server is already running
        if not self.is_run:
            # set class attribute
            ThreadingTCPServer.address_family = socket.AF_INET6 if self.ipv6 else socket.AF_INET
            ThreadingTCPServer.daemon_threads = True
            # init server
            self._service = ThreadingTCPServer((self.host, self.port), self.ModbusService, bind_and_activate=False)
            # pass some things shared with server threads (access via self.server in ModbusService.handle())
            self._service.evt_running = self._evt_running
            self._service.engine = self._engine
            # set socket options
            self._service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # TODO test no_delay with bench
            self._service.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # bind and activate
            try:
                self._service.server_bind()
                self._service.server_activate()
            except OSError as e:
                raise ModbusServer.NetworkError(e)
            # serve request
            if self.no_block:
                self._serve_th = Thread(target=self._serve)
                self._serve_th.daemon = True
                self._serve_th.start()
            else:
                self._serve()

    def stop(self):
        """Stop the server."""
        if self.is_run:
            self._service.shutdown()
            self._service.server_close()

    @property
    def is_run(self):
        """Return True if server running.

        """
        return self._evt_running.is_set()

    def _serve(self):
        try:
            self._evt_running.set()
            self._service.serve_forever()
        except Exception:
            self._service.server_close()
            raise
        except KeyboardInterrupt:
            self._service.server_close()
        finally:
            self._evt_running.clear()
