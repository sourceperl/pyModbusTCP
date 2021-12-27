# Python module: ModbusServer class (ModBus/TCP Server)

from .constants import READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS, \
    WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS, WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, \
    EXP_NONE, EXP_ILLEGAL_FUNCTION, EXP_DATA_ADDRESS, EXP_DATA_VALUE, \
    MODBUS_PORT
from .utils import test_bit, set_bit
import socket
import struct
from threading import Lock, Thread
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
    def __init__(self, client_addr, client_port, rx_mbap, rx_pdu):
        self.client_addr = client_addr
        self.client_port = client_port
        self.rx_mbap = rx_mbap
        self.rx_pdu = rx_pdu

    @property
    def rx_frame(self):
        return self.rx_mbap + self.rx_pdu

    @property
    def rx_frame_as_str(self):
        mbap_str = ' '.join(['%02X' % c for c in bytearray(self.rx_mbap)])
        pdu_str = ' '.join(['%02X' % c for c in bytearray(self.rx_pdu)])
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
            raise ValueError('conf is invalid')
        # private
        self._coils_lock = Lock()
        self._coils = [self.conf.coils_default_value] * self.conf.coils_size
        self._d_inputs_lock = Lock()
        self._d_inputs = [self.conf.d_inputs_default_value] * self.conf.d_inputs_size
        self._h_regs_lock = Lock()
        self._h_regs = [self.conf.h_regs_default_value] * self.conf.h_regs_size
        self._i_regs_lock = Lock()
        self._i_regs = [self.conf.i_regs_default_value] * self.conf.i_regs_size

    def get_coils(self, address, number=1, _srv_infos=None):
        """Read data on server coils space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :param _srv_infos: some server infos (must be set by server only)
        :type _srv_infos: ModbusServerInfos
        :returns: list of bool or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._coils_lock:
            if (address >= 0) and (address + number <= len(self._coils)):
                return self._coils[address: number + address]
            else:
                return None

    def set_coils(self, address, bit_list, _srv_infos=None):
        """Write data to server coils space

        :param address: start address
        :type address: int
        :param bit_list: a list of bool to write
        :type bit_list: list
        :param _srv_infos: some server infos (must be set by server only)
        :type _srv_infos: ModbusServerInfos
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
        if _srv_infos:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_coils_change(address, from_value, to_value, srv_infos=_srv_infos)
        return True

    def get_discrete_inputs(self, address, number=1, _srv_infos=None):
        """Read data on server discrete inputs space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :param _srv_infos: some server infos (must be set by server only)
        :type _srv_infos: ModbusServerInfos
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

    def get_holding_registers(self, address, number=1, _srv_infos=None):
        """Read data on server holding registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :param _srv_infos: some server infos (must be set by server only)
        :type _srv_infos: ModbusServerInfos
        :returns: list of int or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._h_regs_lock:
            if (address >= 0) and (address + number <= len(self._h_regs)):
                return self._h_regs[address: number + address]
            else:
                return None

    def set_holding_registers(self, address, word_list, _srv_infos=None):
        """Write data to server holding registers space

        :param address: start address
        :type address: int
        :param word_list: a list of word to write
        :type word_list: list
        :param _srv_infos: some server infos (must be set by server only)
        :type _srv_infos: ModbusServerInfos
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
        if _srv_infos:
            # notify changes with on change method (after atomic update)
            for address, from_value, to_value in changes_list:
                self.on_holding_registers_change(address, from_value, to_value, srv_infos=_srv_infos)
        return True

    def get_input_registers(self, address, number=1, _srv_infos=None):
        """Read data on server input registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :param _srv_infos: some server infos (must be set by server only)
        :type _srv_infos: ModbusServerInfos
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
            raise ValueError('data_bank is invalid')

    def read_coils(self, address, count, srv_infos):
        # read bits from DataBank
        bits_l = self.data_bank.get_coils(address, count, _srv_infos=srv_infos)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def write_coils(self, address, bits_l, srv_infos):
        # write bits to DataBank
        update_ok = self.data_bank.set_coils(address, bits_l, _srv_infos=srv_infos)
        # return DataStatus to server
        if update_ok:
            return DataHandlerReturn(exp_code=EXP_NONE)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_d_inputs(self, address, count, srv_infos):
        # read bits from DataBank
        bits_l = self.data_bank.get_discrete_inputs(address, count, _srv_infos=srv_infos)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_h_regs(self, address, count, srv_infos):
        # read words from DataBank
        words_l = self.data_bank.get_holding_registers(address, count, _srv_infos=srv_infos)
        # return DataStatus to server
        if words_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def write_h_regs(self, address, words_l, srv_infos):
        # write words to DataBank
        update_ok = self.data_bank.set_holding_registers(address, words_l, _srv_infos=srv_infos)
        # return DataStatus to server
        if update_ok:
            return DataHandlerReturn(exp_code=EXP_NONE)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_i_regs(self, address, count, srv_infos):
        # read words from DataBank
        words_l = self.data_bank.get_input_registers(address, count, _srv_infos=srv_infos)
        # return DataStatus to server
        if words_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)


class ModbusServer:
    """ Modbus TCP server """

    class ModbusService(BaseRequestHandler):

        def _recv_all(self, size):
            # on platform with MSG_WAITALL flag: use it
            if hasattr(socket, 'MSG_WAITALL'):
                data = self.request.recv(size, socket.MSG_WAITALL)
            # on platform (like Windows) without MSG_WAITALL: emulate it
            else:
                data = b''
                while len(data) < size:
                    data += self.request.recv(size - len(data))
            return data

        def handle(self):
            while True:
                rx_mbap = self._recv_all(7)
                # close connection if no standard 7 bytes header
                if not (rx_mbap and len(rx_mbap) == 7):
                    break
                # decode header
                (rx_mbap_tr_id, rx_mbap_pr_id,
                 rx_mbap_length, rx_mbap_unit_id) = struct.unpack('>HHHB', rx_mbap)
                # close connection if frame header content inconsistency
                if not ((rx_mbap_pr_id == 0) and (2 < rx_mbap_length < 256)):
                    break
                # receive pdu
                rx_pdu = self._recv_all(rx_mbap_length - 1)
                # close connection if lack of bytes in frame pdu
                if not (rx_pdu and (len(rx_pdu) == rx_mbap_length - 1)):
                    break
                # enforce bytearray for py2
                rx_pdu = bytearray(rx_pdu)
                # pdu decode: function code
                rx_func_code = struct.unpack('B', rx_pdu[0:1])[0]
                # close connection if function code is inconsistent
                if rx_func_code > 0x7F:
                    break
                # some default value
                exp_status = EXP_NONE
                tx_pdu = b''
                # set modbus server infos
                client_addr, client_port = self.request.getpeername()
                srv_infos = ModbusServerInfos(client_addr=client_addr, client_port=client_port,
                                              rx_mbap=rx_mbap, rx_pdu=rx_pdu)
                # functions Read Coils (0x01) or Read Discrete Inputs (0x02)
                if rx_func_code in (READ_COILS, READ_DISCRETE_INPUTS):
                    # ensure pdu size
                    if len(rx_pdu[1:]) != struct.calcsize('>HH'):
                        break
                    # decode pdu
                    (start_addr, quantity_bits) = struct.unpack('>HH', rx_pdu[1:])
                    # check quantity of requested bits
                    if 0x0001 <= quantity_bits <= 0x07D0:
                        # data handler read request: for coils or discrete inputs space
                        if rx_func_code == READ_COILS:
                            ret = self.server.data_hdl.read_coils(start_addr, quantity_bits, srv_infos=srv_infos)
                        else:
                            ret = self.server.data_hdl.read_d_inputs(start_addr, quantity_bits, srv_infos=srv_infos)
                        # format regular or except response
                        if ret.ok:
                            # allocate bytes list
                            b_size = int(quantity_bits / 8)
                            b_size += 1 if (quantity_bits % 8) else 0
                            bytes_l = [0] * b_size
                            # populate bytes list with data bank bits
                            for i, item in enumerate(ret.data):
                                if item:
                                    bytes_l[i//8] = set_bit(bytes_l[i//8], i % 8)
                            # build pdu
                            tx_pdu += struct.pack('BB', rx_func_code, len(bytes_l))
                            # add requested bits
                            tx_pdu += struct.pack('%dB' % len(bytes_l), *bytes_l)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                # functions Read Holding Registers (0x03) or Read Input Registers (0x04)
                elif rx_func_code in (READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS):
                    # ensure pdu size
                    if len(rx_pdu[1:]) != struct.calcsize('>HH'):
                        break
                    # decode pdu
                    (start_addr, quantity_regs) = struct.unpack('>HH', rx_pdu[1:])
                    # check quantity of requested words
                    if 0x0001 <= quantity_regs <= 0x007D:
                        # data handler read request: for holding or input registers space
                        if rx_func_code == READ_HOLDING_REGISTERS:
                            ret = self.server.data_hdl.read_h_regs(start_addr, quantity_regs, srv_infos=srv_infos)
                        else:
                            ret = self.server.data_hdl.read_i_regs(start_addr, quantity_regs, srv_infos=srv_infos)
                        # format regular or except response
                        if ret.ok:
                            # build pdu
                            tx_pdu += struct.pack('BB', rx_func_code, quantity_regs * 2)
                            # add requested words
                            tx_pdu += struct.pack('>%dH' % len(ret.data), *ret.data)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                # function Write Single Coil (0x05)
                elif rx_func_code is WRITE_SINGLE_COIL:
                    # ensure pdu size
                    if len(rx_pdu[1:]) != struct.calcsize('>HH'):
                        break
                    # decode pdu
                    (coil_addr, coil_value) = struct.unpack('>HH', rx_pdu[1:])
                    # format coil raw value to bool
                    coil_as_bool = bool(coil_value == 0xFF00)
                    # data handler update request
                    ret = self.server.data_hdl.write_coils(coil_addr, [coil_as_bool], srv_infos=srv_infos)
                    # format regular or except response
                    if ret.ok:
                        tx_pdu += struct.pack('>BHH', rx_func_code, coil_addr, coil_value)
                    else:
                        exp_status = ret.exp_code
                # function Write Single Register (0x06)
                elif rx_func_code is WRITE_SINGLE_REGISTER:
                    # ensure pdu size
                    if len(rx_pdu[1:]) != struct.calcsize('>HH'):
                        break
                    # decode pdu
                    (reg_addr, reg_value) = struct.unpack('>HH', rx_pdu[1:])
                    # data handler update request
                    ret = self.server.data_hdl.write_h_regs(reg_addr, [reg_value], srv_infos=srv_infos)
                    # format regular or except response
                    if ret.ok:
                        tx_pdu += struct.pack('>BHH', rx_func_code, reg_addr, reg_value)
                    else:
                        exp_status = ret.exp_code
                # function Write Multiple Coils (0x0F)
                elif rx_func_code is WRITE_MULTIPLE_COILS:
                    # ensure pdu size
                    if len(rx_pdu[1:6]) != struct.calcsize('>HHB'):
                        break
                    # decode pdu
                    (start_addr, quantity_bits, byte_count) = struct.unpack('>HHB', rx_pdu[1:6])
                    # ensure minimal pdu size for data part
                    if len(rx_pdu[6:]) < byte_count:
                        break
                    # check quantity of updated coils
                    if (0x0001 <= quantity_bits <= 0x07B0) and (byte_count >= (quantity_bits / 8)):
                        # allocate bits list
                        bits_l = [False] * quantity_bits
                        # populate bits list with bits from rx frame
                        for i, _ in enumerate(bits_l):
                            bit_val = rx_pdu[i//8 + 6]
                            bits_l[i] = test_bit(bit_val, i % 8)
                        # data handler update request
                        ret = self.server.data_hdl.write_coils(start_addr, bits_l, srv_infos=srv_infos)
                        # format regular or except response
                        if ret.ok:
                            tx_pdu += struct.pack('>BHH', rx_func_code, start_addr, quantity_bits)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                # function Write Multiple Registers (0x10)
                elif rx_func_code is WRITE_MULTIPLE_REGISTERS:
                    # ensure pdu size
                    if len(rx_pdu[1:6]) != struct.calcsize('>HHB'):
                        break
                    # decode pdu
                    (start_addr, quantity_regs, byte_count) = struct.unpack('>HHB', rx_pdu[1:6])
                    # ensure minimal pdu size for data part
                    if len(rx_pdu[6:]) < byte_count:
                        break
                    # check quantity of updated words
                    if (0x0001 <= quantity_regs <= 0x007B) and (byte_count == quantity_regs * 2):
                        # allocate words list
                        regs_l = [0] * quantity_regs
                        # populate words list with words from rx frame
                        for i, _ in enumerate(regs_l):
                            offset = i * 2 + 6
                            regs_l[i] = struct.unpack('>H', rx_pdu[offset:offset + 2])[0]
                        # data handler update request
                        ret = self.server.data_hdl.write_h_regs(start_addr, regs_l, srv_infos=srv_infos)
                        # format regular or except response
                        if ret.ok:
                            tx_pdu += struct.pack('>BHH', rx_func_code, start_addr, quantity_regs)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                else:
                    exp_status = EXP_ILLEGAL_FUNCTION
                # check exception
                if exp_status != EXP_NONE:
                    # build pdu with exception status
                    tx_pdu += struct.pack('BB', rx_func_code + 0x80, exp_status)
                # build mbap
                tx_mbap = struct.pack('>HHHB', rx_mbap_tr_id, rx_mbap_pr_id, len(tx_pdu) + 1, rx_mbap_unit_id)
                # send frame
                self.request.send(tx_mbap + tx_pdu)
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
        self._running = False
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
            # pass data handler for server threads (access via self.server in ModbusService.handle())
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
        return self._running

    def _serve(self):
        try:
            self._running = True
            self._service.serve_forever()
        except Exception:
            self._service.server_close()
            raise
        finally:
            self._running = False
