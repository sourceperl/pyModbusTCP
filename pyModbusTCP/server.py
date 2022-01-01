# Python module: ModbusServer class (ModBus/TCP Server)

from .constants import READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS, \
    WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS, WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, \
    EXP_NONE, EXP_ILLEGAL_FUNCTION, EXP_DATA_ADDRESS, EXP_DATA_VALUE, \
    MODBUS_PORT
from .utils import test_bit, set_bit
from random import randint
import socket
import struct
from threading import Lock, Thread, Event
from warnings import warn

# python2 compatibility
try:
    from socketserver import BaseRequestHandler, ThreadingTCPServer
except ImportError:
    from SocketServer import BaseRequestHandler, ThreadingTCPServer


class DataBank:
    """ This is historical data class provide for warn about DataBank change """

    WARN_MSG = 'This class is deprecated, use ModbusServerDataBank instance instead.'

    @classmethod
    def get_bits(cls, *_args, **_kwargs):
        warn(cls.WARN_MSG, DeprecationWarning, stacklevel=2)

    @classmethod
    def set_bits(cls, *_args, **_kwargs):
        warn(cls.WARN_MSG, DeprecationWarning, stacklevel=2)

    @classmethod
    def get_words(cls, *_args, **_kwargs):
        warn(cls.WARN_MSG, DeprecationWarning, stacklevel=2)

    @classmethod
    def set_words(cls, *_args, **_kwargs):
        warn(cls.WARN_MSG, DeprecationWarning, stacklevel=2)


class DataHandlerReturn:
    def __init__(self, exp_code, data=None):
        self.exp_code = exp_code
        self.data = data

    @property
    def ok(self):
        return self.exp_code == EXP_NONE


class ModbusServerInfos:
    def __init__(self):
        self.client_addr = ''
        self.client_port = 502
        self.client_raw_mbap = bytearray()
        self.client_raw_pdu = bytearray()

    @property
    def client_raw_frame(self):
        return self.client_raw_mbap + self.client_raw_pdu

    @property
    def client_raw_frame_as_str(self):
        mbap_str = ' '.join(['%02X' % c for c in bytearray(self.client_raw_mbap)])
        pdu_str = ' '.join(['%02X' % c for c in bytearray(self.client_raw_pdu)])
        return '[%s] %s' % (mbap_str, pdu_str)


class ModbusServerDataBank:
    """ Class for thread safe access to data space """

    class Conf:
        def __init__(self, coils_size=0x10000, coils_default_value=False,
                     d_inputs_size=0x10000, d_inputs_default_value=False,
                     h_regs_size=0x10000, h_regs_default_value=0,
                     i_regs_size=0x10000, i_regs_default_value=0,
                     virtual_mode=False):
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

    def __init__(self, conf=None):
        """Constructor

        Modbus server data bank constructor.

        :param conf: Modbus server data bank configuration (optional)
        :type conf: ModbusServerDataBank.Conf
        """
        # public
        if conf is None:
            self.conf = ModbusServerDataBank.Conf()
        elif isinstance(conf, ModbusServerDataBank.Conf):
            self.conf = conf
        else:
            raise ValueError('conf arg is invalid')
        # private
        self._coils_lock = Lock()
        self._coils = [self.conf.coils_default_value] * self.conf.coils_size
        self._d_inputs_lock = Lock()
        self._d_inputs = [self.conf.d_inputs_default_value] * self.conf.d_inputs_size
        self._h_regs_lock = Lock()
        self._h_regs = [self.conf.h_regs_default_value] * self.conf.h_regs_size
        self._i_regs_lock = Lock()
        self._i_regs = [self.conf.i_regs_default_value] * self.conf.i_regs_size

    def get_coils(self, address, number=1, srv_infos=None):
        """Read data on server coils space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :param srv_infos: some server infos (must be set by server only)
        :type srv_infos: ModbusServerInfos
        :returns: list of bool or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._coils_lock:
            if (address >= 0) and (address + number <= len(self._coils)):
                return self._coils[address: number + address]
            else:
                return None

    def set_coils(self, address, bit_list, srv_infos=None):
        """Write data to server coils space

        :param address: start address
        :type address: int
        :param bit_list: a list of bool to write
        :type bit_list: list
        :param srv_infos: some server infos (must be set by server only)
        :type srv_infos: ModbusServerInfos
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if bit_list members cannot be convert to bool
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
        if srv_infos:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_coils_change(address, from_value, to_value, srv_infos=srv_infos)
        return True

    def get_discrete_inputs(self, address, number=1, srv_infos=None):
        """Read data on server discrete inputs space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :param srv_infos: some server infos (must be set by server only)
        :type srv_infos: ModbusServerInfos
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
        :raises ValueError: if bit_list members cannot be convert to bool
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

    def get_holding_registers(self, address, number=1, srv_infos=None):
        """Read data on server holding registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :param srv_infos: some server infos (must be set by server only)
        :type srv_infos: ModbusServerInfos
        :returns: list of int or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._h_regs_lock:
            if (address >= 0) and (address + number <= len(self._h_regs)):
                return self._h_regs[address: number + address]
            else:
                return None

    def set_holding_registers(self, address, word_list, srv_infos=None):
        """Write data to server holding registers space

        :param address: start address
        :type address: int
        :param word_list: a list of word to write
        :type word_list: list
        :param srv_infos: some server infos (must be set by server only)
        :type srv_infos: ModbusServerInfos
        :returns: True if success or None if error
        :rtype: bool or None
        :raises ValueError: if word_list members cannot be convert to int
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
        if srv_infos:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_holding_registers_change(address, from_value, to_value, srv_infos=srv_infos)
        return True

    def get_input_registers(self, address, number=1, srv_infos=None):
        """Read data on server input registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :param srv_infos: some server infos (must be set by server only)
        :type srv_infos: ModbusServerInfos
        :returns: list of int or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._i_regs_lock:
            if (address >= 0) and (address + number <= len(self._h_regs)):
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
        :raises ValueError: if word_list members cannot be convert to int
        """
        # ensure word_list values are int with a max bit length of 16
        word_list = [int(w) & 0xffff for w in word_list]
        # ensure atomic update of internal data
        with self._i_regs_lock:
            if (address >= 0) and (address + len(word_list) <= len(self._h_regs)):
                for offset, c_value in enumerate(word_list):
                    c_address = address + offset
                    if self._i_regs[c_address] != c_value:
                        self._i_regs[c_address] = c_value
            else:
                return None
        return True

    def on_coils_change(self, address, from_value, to_value, srv_infos):
        """Call by server when a value change occur in coils space

        This method is provide to be overridden with user code to catch changes

        :param address: address of coil
        :type address: int
        :param from_value: coil original value
        :type from_value: bool
        :param to_value: coil next value
        :type to_value: bool
        :param srv_infos: some server infos
        :type srv_infos: ModbusServerInfos
        """
        pass

    def on_holding_registers_change(self, address, from_value, to_value, srv_infos):
        """Call by server when a value change occur in holding registers space

        This method is provide to be overridden with user code to catch changes

        :param address: address of register
        :type address: int
        :param from_value: register original value
        :type from_value: int
        :param to_value: register next value
        :type to_value: int
        :param srv_infos: some server infos
        :type srv_infos: ModbusServerInfos
        """
        pass


class ModbusServerDataHandler:
    """Default data handler for ModbusServer, map server threads calls to DataBank.

    Custom handler must derive from this class.
    """

    def __init__(self, data_bank=None):
        """Constructor

        Modbus server data handler constructor.

        :param data_bank: a reference to custom DefaultDataBank
        :type data_bank: ModbusServerDataBank
        """
        if data_bank is None:
            self.data_bank = ModbusServerDataBank()
        elif isinstance(data_bank, ModbusServerDataBank):
            self.data_bank = data_bank
        else:
            raise ValueError('data_bank arg is invalid')

    def read_coils(self, address, count, srv_infos):
        # read bits from DataBank
        bits_l = self.data_bank.get_coils(address, count, srv_infos=srv_infos)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def write_coils(self, address, bits_l, srv_infos):
        # write bits to DataBank
        update_ok = self.data_bank.set_coils(address, bits_l, srv_infos=srv_infos)
        # return DataStatus to server
        if update_ok:
            return DataHandlerReturn(exp_code=EXP_NONE)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_d_inputs(self, address, count, srv_infos):
        # read bits from DataBank
        bits_l = self.data_bank.get_discrete_inputs(address, count, srv_infos=srv_infos)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_h_regs(self, address, count, srv_infos):
        # read words from DataBank
        words_l = self.data_bank.get_holding_registers(address, count, srv_infos=srv_infos)
        # return DataStatus to server
        if words_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def write_h_regs(self, address, words_l, srv_infos):
        # write words to DataBank
        update_ok = self.data_bank.set_holding_registers(address, words_l, srv_infos=srv_infos)
        # return DataStatus to server
        if update_ok:
            return DataHandlerReturn(exp_code=EXP_NONE)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_i_regs(self, address, count, srv_infos):
        # read words from DataBank
        words_l = self.data_bank.get_input_registers(address, count, srv_infos=srv_infos)
        # return DataStatus to server
        if words_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)


class ModbusServer:
    """ Modbus TCP server """

    class InternalError(Exception):
        pass

    class MBAP:
        def __init__(self, raw=None):
            # public
            self.transaction_id = randint(0, 0xffff)
            self.protocol_id = 0
            self.length = 0
            self.unit_id = 1
            # if raw arg is define, decode it now
            if raw is not None:
                self.raw_decode(raw)

        @property
        def raw(self):
            try:
                return struct.pack('>HHHB', self.transaction_id,
                                   self.protocol_id, self.length,
                                   self.unit_id)
            except struct.error as e:
                raise ModbusServer.InternalError('MBAP raw encode pack error: %s' % e)

        def raw_decode(self, value):
            # close connection if no standard 7 bytes mbap header
            if not (value and len(value) == 7):
                raise ModbusServer.InternalError('MBAP must have a length of 7 bytes')
            # decode header
            (self.transaction_id, self.protocol_id,
             self.length, self.unit_id) = struct.unpack('>HHHB', value)
            # check frame header content inconsistency
            if self.protocol_id != 0:
                raise ModbusServer.InternalError('MBAP protocol ID must be 0')
            if not 2 < self.length < 256:
                raise ModbusServer.InternalError('MBAP length must be between 2 and 256')

        def raw_with_pdu(self, pdu):
            self.length = len(pdu) + 1
            return self.raw + pdu.raw

    class PDU:
        def __init__(self, raw=b''):
            # enforce bytearray for py2
            self.raw = bytearray(raw)

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

        def clean(self):
            self.raw = bytearray()

        def build_except(self, func_code, exp_status):
            self.clean()
            self.add_pack('BB', func_code + 0x80, exp_status)
            return self

        def add_pack(self, fmt, *args):
            try:
                self.raw += bytearray(struct.pack(fmt, *args))
            except struct.error:
                err_msg = 'unable to format PDU message (fmt: %s, values: %s)' % (fmt, args)
                raise ModbusServer.InternalError(err_msg)

        def unpack(self, fmt, from_byte=None, to_byte=None):
            raw_section = self.raw[from_byte:to_byte]
            try:
                return struct.unpack(fmt, raw_section)
            except struct.error:
                err_msg = 'unable to decode PDU message  (fmt: %s, values: %s)' % (fmt, raw_section)
                raise ModbusServer.InternalError(err_msg)

    class ModbusService(BaseRequestHandler):

        @property
        def server_running(self):
            return self.server.evt_running.is_set()

        def _send_all(self, data, flags=0):
            try:
                self.request.sendall(data, flags)
                return True
            except socket.timeout:
                return False

        def _recv_all(self, size):
            data = b''
            while len(data) < size:
                try:
                    # avoid to keep this TCP thread run after server.stop() on main server
                    if not self.server_running:
                        raise ModbusServer.InternalError('main server is not running')
                    # recv all data or a chunk of it
                    data += self.request.recv(size - len(data))
                except socket.timeout:
                    # just redo main server run test and recv operations on timeout
                    pass
            return bytearray(data)

        def _read_bits(self, rx_pdu):
            # functions Read Coils (0x01) or Read Discrete Inputs (0x02)
            # init PDU() for return value
            ret_pdu = ModbusServer.PDU()
            # decode pdu
            (start_addr, quantity_bits) = rx_pdu.unpack('>HH', from_byte=1, to_byte=5)
            # check quantity of requested bits
            if 0x0001 <= quantity_bits <= 0x07D0:
                # data handler read request: for coils or discrete inputs space
                if rx_pdu.func_code == READ_COILS:
                    ret_hdl = self.server.data_hdl.read_coils(start_addr, quantity_bits, srv_infos=self.srv_infos)
                else:
                    ret_hdl = self.server.data_hdl.read_d_inputs(start_addr, quantity_bits, srv_infos=self.srv_infos)
                # format regular or except response
                if ret_hdl.ok:
                    # allocate bytes list
                    b_size = int(quantity_bits / 8)
                    b_size += 1 if (quantity_bits % 8) else 0
                    bytes_l = [0] * b_size
                    # populate bytes list with data bank bits
                    for i, item in enumerate(ret_hdl.data):
                        if item:
                            bytes_l[i // 8] = set_bit(bytes_l[i // 8], i % 8)
                    # build pdu
                    ret_pdu.add_pack('BB', rx_pdu.func_code, len(bytes_l))
                    ret_pdu.add_pack('%dB' % len(bytes_l), *bytes_l)
                else:
                    ret_pdu.build_except(rx_pdu.func_code, ret_hdl.exp_code)
            else:
                ret_pdu.build_except(rx_pdu.func_code, EXP_DATA_VALUE)
            return ret_pdu

        def _read_words(self, rx_pdu):
            # functions Read Holding Registers (0x03) or Read Input Registers (0x04)
            # init PDU() for return value
            ret_pdu = ModbusServer.PDU()
            # decode pdu
            (start_addr, quantity_regs) = rx_pdu.unpack('>HH', from_byte=1, to_byte=5)
            # check quantity of requested words
            if 0x0001 <= quantity_regs <= 0x007D:
                # data handler read request: for holding or input registers space
                if rx_pdu.func_code == READ_HOLDING_REGISTERS:
                    ret_hdl = self.server.data_hdl.read_h_regs(start_addr, quantity_regs, srv_infos=self.srv_infos)
                else:
                    ret_hdl = self.server.data_hdl.read_i_regs(start_addr, quantity_regs, srv_infos=self.srv_infos)
                # format regular or except response
                if ret_hdl.ok:
                    # build pdu
                    ret_pdu.add_pack('BB', rx_pdu.func_code, quantity_regs * 2)
                    # add_pack requested words
                    ret_pdu.add_pack('>%dH' % len(ret_hdl.data), *ret_hdl.data)
                else:
                    ret_pdu.build_except(rx_pdu.func_code, ret_hdl.exp_code)
            else:
                ret_pdu.build_except(rx_pdu.func_code, EXP_DATA_VALUE)
            return ret_pdu

        def _write_single_coil(self, rx_pdu):
            # function Write Single Coil (0x05)
            # init PDU() for return value
            ret_pdu = ModbusServer.PDU()
            # decode pdu
            (coil_addr, coil_value) = rx_pdu.unpack('>HH', from_byte=1, to_byte=5)
            # format coil raw value to bool
            coil_as_bool = bool(coil_value == 0xFF00)
            # data handler update request
            ret_hdl = self.server.data_hdl.write_coils(coil_addr, [coil_as_bool], srv_infos=self.srv_infos)
            # format regular or except response
            if ret_hdl.ok:
                ret_pdu.add_pack('>BHH', rx_pdu.func_code, coil_addr, coil_value)
            else:
                ret_pdu.build_except(rx_pdu.func_code, ret_hdl.exp_code)
            return ret_pdu

        def _write_single_register(self, rx_pdu):
            # function Write Single Register (0x06)
            # init PDU() for return value
            ret_pdu = ModbusServer.PDU()
            # decode pdu
            (reg_addr, reg_value) = rx_pdu.unpack('>HH', from_byte=1, to_byte=5)
            # data handler update request
            ret_hdl = self.server.data_hdl.write_h_regs(reg_addr, [reg_value], srv_infos=self.srv_infos)
            # format regular or except response
            if ret_hdl.ok:
                ret_pdu.add_pack('>BHH', rx_pdu.func_code, reg_addr, reg_value)
            else:
                ret_pdu.build_except(rx_pdu.func_code, ret_hdl.exp_code)
            return ret_pdu

        def _write_multiple_coils(self, rx_pdu):
            # function Write Multiple Coils (0x0F)
            # init PDU() for return value
            ret_pdu = ModbusServer.PDU()
            # decode pdu
            (start_addr, quantity_bits, byte_count) = rx_pdu.unpack('>HHB', from_byte=1, to_byte=6)
            # check quantity of updated coils and ensure minimal pdu size
            bytes_need = quantity_bits // 8 + (1 if quantity_bits % 8 else 0)
            if 0x0001 <= quantity_bits <= 0x07B0 and \
               bytes_need <= byte_count <= len(rx_pdu.raw[6:]):
                # allocate bits list
                bits_l = [False] * quantity_bits
                # populate bits list with bits from rx frame
                for i, _ in enumerate(bits_l):
                    bit_val = rx_pdu.raw[i // 8 + 6]
                    bits_l[i] = test_bit(bit_val, i % 8)
                # data handler update request
                ret_hdl = self.server.data_hdl.write_coils(start_addr, bits_l, srv_infos=self.srv_infos)
                # format regular or except response
                if ret_hdl.ok:
                    ret_pdu.add_pack('>BHH', rx_pdu.func_code, start_addr, quantity_bits)
                else:
                    ret_pdu.build_except(rx_pdu.func_code, ret_hdl.exp_code)
            else:
                ret_pdu.build_except(rx_pdu.func_code, EXP_DATA_VALUE)
            return ret_pdu

        def _write_multiple_registers(self, rx_pdu):
            # function Write Multiple Registers (0x10)
            # init PDU() for return value
            ret_pdu = ModbusServer.PDU()
            # decode pdu
            (start_addr, quantity_regs, byte_count) = rx_pdu.unpack('>HHB', from_byte=1, to_byte=6)
            # check quantity of updated registers and ensure minimal pdu size
            if 0x0001 <= quantity_regs <= 0x007B and byte_count == quantity_regs * 2 and \
               len(rx_pdu.raw[6:]) >= byte_count:
                # allocate words list
                regs_l = [0] * quantity_regs
                # populate words list with words from rx frame
                for i, _ in enumerate(regs_l):
                    offset = i * 2 + 6
                    regs_l[i] = rx_pdu.unpack('>H', from_byte=offset, to_byte=offset + 2)[0]
                # data handler update request
                ret_hdl = self.server.data_hdl.write_h_regs(start_addr, regs_l, srv_infos=self.srv_infos)
                # format regular or except response
                if ret_hdl.ok:
                    ret_pdu.add_pack('>BHH', rx_pdu.func_code, start_addr, quantity_regs)
                else:
                    ret_pdu.build_except(rx_pdu.func_code, ret_hdl.exp_code)
            else:
                ret_pdu.build_except(rx_pdu.func_code, EXP_DATA_VALUE)
            return ret_pdu

        def setup(self):
            # set a socket timeout of 1s on blocking operations (like send/recv)
            # this avoid hang thread deletion when main server exit (see _recv_all method)
            self.request.settimeout(1)
            # init and update server infos structure
            self.srv_infos = ModbusServerInfos()
            (addr, port) = self.request.getpeername()
            self.srv_infos.client_addr = addr
            self.srv_infos.client_port = port

        def handle(self):
            # close current socket on error or ThreadExit custom except
            try:
                # main processing loop
                while True:
                    # receive mbap from client
                    raw_mbap = self._recv_all(7)
                    rx_mbap = ModbusServer.MBAP(raw=raw_mbap)
                    # receive pdu from client
                    raw_pdu = self._recv_all(rx_mbap.length - 1)
                    rx_pdu = ModbusServer.PDU(raw=raw_pdu)
                    # set modbus server infos
                    self.srv_infos.client_raw_mbap = raw_mbap
                    self.srv_infos.client_raw_pdu = raw_pdu
                    # modbus functions maps
                    f_maps = {READ_COILS: self._read_bits,
                              READ_DISCRETE_INPUTS: self._read_bits,
                              READ_HOLDING_REGISTERS: self._read_words,
                              READ_INPUT_REGISTERS: self._read_words,
                              WRITE_SINGLE_COIL: self._write_single_coil,
                              WRITE_SINGLE_REGISTER: self._write_single_register,
                              WRITE_MULTIPLE_COILS: self._write_multiple_coils,
                              WRITE_MULTIPLE_REGISTERS: self._write_multiple_registers}
                    # call ad-hoc function (if unavailable send EXP_ILLEGAL_FUNCTION)
                    try:
                        tx_pdu = f_maps[rx_pdu.func_code](rx_pdu)
                    except KeyError:
                        tx_pdu = ModbusServer.PDU().build_except(rx_pdu.func_code, EXP_ILLEGAL_FUNCTION)
                    # send frame
                    self._send_all(rx_mbap.raw_with_pdu(tx_pdu))
            except (ModbusServer.InternalError, socket.error):
                # on main loop except: exit from it and close the current socket
                self.request.close()

    def __init__(self, host='localhost', port=MODBUS_PORT, no_block=False, ipv6=False,
                 data_bank=None, data_hdl=None):
        """Constructor

        Modbus server constructor.

        :param host: hostname or IPv4/IPv6 address server address (optional)
        :type host: str
        :param port: TCP port number (optional)
        :type port: int
        :param no_block: set no block mode, in this mode start() return (optional)
        :type no_block: bool
        :param ipv6: use ipv6 stack
        :type ipv6: bool
        :param data_bank: a reference to custom DefaultDataBank
        :type data_bank: ModbusServerDataBank
        :param data_hdl: a reference to custom ModbusServerDataHandler
        :type data_hdl: ModbusServerDataHandler
        """
        # public
        self.host = host
        self.port = port
        self.no_block = no_block
        self.ipv6 = ipv6
        # default data handler is ModbusServerDataHandler or a child of it
        if data_hdl is None:
            self.data_hdl = ModbusServerDataHandler(data_bank=data_bank)
        elif isinstance(data_hdl, ModbusServerDataHandler):
            self.data_hdl = data_hdl
            if data_bank:
                raise ValueError('when data_hdl is set, you must define data_bank in it')
        else:
            raise ValueError('data_hdl is not a ModbusServerDataHandler (or child of it) instance')
        # data bank shortcut
        self.data_bank = self.data_hdl.data_bank
        # private
        self._evt_running = Event()
        self._service = None
        self._serve_th = None

    def start(self):
        """Start the server.

        This function will block if no_block is not set.
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
            self._service.data_hdl = self.data_hdl
            # set socket options
            self._service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._service.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            # TODO test no_delay with bench
            self._service.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # bind and activate
            self._service.server_bind()
            self._service.server_activate()
            # serve request
            if self.no_block:
                self._serve_th = Thread(target=self._serve)
                self._serve_th.daemon = True
                self._serve_th.start()
            else:
                self._serve()

    def stop(self):
        """Stop the server.

        Do nothing if server is already not running.
        """
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
