# Python module: ModbusServer class (ModBus/TCP Server)

from .constants import READ_COILS, READ_DISCRETE_INPUTS, READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS, \
    WRITE_MULTIPLE_COILS, WRITE_MULTIPLE_REGISTERS, WRITE_SINGLE_COIL, WRITE_SINGLE_REGISTER, \
    EXP_NONE, EXP_ILLEGAL_FUNCTION, EXP_DATA_ADDRESS, EXP_DATA_VALUE, \
    MODBUS_PORT
from .utils import test_bit, set_bit
import socket
import struct
from threading import Lock, Thread
from socketserver import BaseRequestHandler, ThreadingTCPServer
from warnings import warn


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


class ModbusServerDataBank:
    """ Class for thread safe access to data space """

    class Conf:
        def __init__(self, coils_size=0x10000, coils_default_value=False,
                     d_inputs_size = 0x10000, d_inputs_default_value = False,
                     h_regs_size = 0x10000, h_regs_default_value = 0,
                     i_regs_size = 0x10000, i_regs_default_value = 0,
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
            # specific modes (override other values)
            if self.virtual_mode:
                self.coils_size = 0
                self.d_inputs_size = 0
                self.h_regs_size = 0
                self.i_regs_size = 0

    def __init__(self, conf=Conf()):
        # public
        self.conf = conf
        # private
        self._coils_lock = Lock()
        self._coils = [self.conf.coils_default_value] * self.conf.coils_size
        self._d_inputs_lock = Lock()
        self._d_inputs = [self.conf.d_inputs_default_value] * self.conf.d_inputs_size
        self._h_regs_lock = Lock()
        self._h_regs = [self.conf.h_regs_default_value] * self.conf.h_regs_size
        self._i_regs_lock = Lock()
        self._i_regs = [self.conf.i_regs_default_value] * self.conf.i_regs_size

    def get_coils(self, address, number=1):
        """Read data on server coils space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :returns: list of bool or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._coils_lock:
            if (address >= 0) and (address + number <= len(self._coils)):
                return self._coils[address: number + address]
            else:
                return None

    def set_coils(self, address, bit_list):
        """Write data to server coils space

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
        # secure copy of data to list used by server thread
        with self._coils_lock:
            if (address >= 0) and (address + len(bit_list) <= len(self._coils)):
                self._coils[address: address + len(bit_list)] = bit_list
                return True
            else:
                return None

    def get_discrete_inputs(self, address, number=1):
        """Read data on server discrete inputs space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
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
        # secure copy of data to list used by server thread
        with self._d_inputs_lock:
            if (address >= 0) and (address + len(bit_list) <= len(self._coils)):
                self._d_inputs[address: address + len(bit_list)] = bit_list
                return True
            else:
                return None

    def get_holding_registers(self, address, number=1):
        """Read data on server holding registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
        :returns: list of int or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._h_regs_lock:
            if (address >= 0) and (address + number <= len(self._h_regs)):
                return self._h_regs[address: number + address]
            else:
                return None

    def set_holding_registers(self, address, word_list):
        """Write data to server holding registers space

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
        # secure copy of data to list used by server thread
        with self._h_regs_lock:
            if (address >= 0) and (address + len(word_list) <= len(self._h_regs)):
                self._h_regs[address: address + len(word_list)] = word_list
                return True
            else:
                return None

    def get_input_registers(self, address, number=1):
        """Read data on server input registers space

        :param address: start address
        :type address: int
        :param number: number of words (optional)
        :type number: int
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
        # secure copy of data to list used by server thread
        with self._i_regs_lock:
            if (address >= 0) and (address + len(word_list) <= len(self._h_regs)):
                self._i_regs[address: address + len(word_list)] = word_list
                return True
            else:
                return None


class DataHandlerReturn:
    def __init__(self, exp_code, data=None):
        self.exp_code = exp_code
        self.data = data

    @property
    def ok(self):
        return self.exp_code == EXP_NONE


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

    def read_coils(self, address, count):
        # read bits from DataBank
        bits_l = self.data_bank.get_coils(address, count)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def write_coils(self, address, bits_l):
        # write bits to DataBank
        update_ok = self.data_bank.set_coils(address, bits_l)
        # return DataStatus to server
        if update_ok:
            return DataHandlerReturn(exp_code=EXP_NONE)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_d_inputs(self, address, count):
        # read bits from DataBank
        bits_l = self.data_bank.get_discrete_inputs(address, count)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_h_regs(self, address, count):
        # read words from DataBank
        words_l = self.data_bank.get_holding_registers(address, count)
        # return DataStatus to server
        if words_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def write_h_regs(self, address, words_l):
        # write words to DataBank
        update_ok = self.data_bank.set_holding_registers(address, words_l)
        # return DataStatus to server
        if update_ok:
            return DataHandlerReturn(exp_code=EXP_NONE)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)

    def read_i_regs(self, address, count):
        # read words from DataBank
        words_l = self.data_bank.get_input_registers(address, count)
        # return DataStatus to server
        if words_l is not None:
            return DataHandlerReturn(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandlerReturn(exp_code=EXP_DATA_ADDRESS)


class ModbusServer:
    """ Modbus TCP server """

    class ModbusService(BaseRequestHandler):

        def recv_all(self, size):
            if hasattr(socket, "MSG_WAITALL"):
                data = self.request.recv(size, socket.MSG_WAITALL)
            else:
                # Windows lacks MSG_WAITALL
                data = b''
                while len(data) < size:
                    data += self.request.recv(size - len(data))
            return data

        def handle(self):
            while True:
                rx_head = self.recv_all(7)
                # close connection if no standard 7 bytes header
                if not (rx_head and len(rx_head) == 7):
                    break
                # decode header
                (rx_hd_tr_id, rx_hd_pr_id,
                 rx_hd_length, rx_hd_unit_id) = struct.unpack('>HHHB', rx_head)
                # close connection if frame header content inconsistency
                if not ((rx_hd_pr_id == 0) and (2 < rx_hd_length < 256)):
                    break
                # receive body
                rx_body = self.recv_all(rx_hd_length - 1)
                # close connection if lack of bytes in frame body
                if not (rx_body and (len(rx_body) == rx_hd_length - 1)):
                    break
                # body decode: function code
                rx_bd_fc = struct.unpack('B', rx_body[0:1])[0]
                # close connection if function code is inconsistent
                if rx_bd_fc > 0x7F:
                    break
                # some default value
                exp_status = EXP_NONE
                tx_body = b''
                # functions Read Coils (0x01) or Read Discrete Inputs (0x02)
                if rx_bd_fc in (READ_COILS, READ_DISCRETE_INPUTS):
                    (b_address, b_count) = struct.unpack('>HH', rx_body[1:])
                    # check quantity of requested bits
                    if 0x0001 <= b_count <= 0x07D0:
                        if rx_bd_fc == READ_COILS:
                            ret = self.server.data_hdl.read_coils(b_address, b_count)
                        else:
                            ret = self.server.data_hdl.read_d_inputs(b_address, b_count)
                        if ret.ok:
                            # allocate bytes list
                            b_size = int(b_count / 8)
                            b_size += 1 if (b_count % 8) else 0
                            bytes_l = [0] * b_size
                            # populate bytes list with data bank bits
                            for i, item in enumerate(ret.data):
                                if item:
                                    byte_i = int(i / 8)
                                    bytes_l[byte_i] = set_bit(bytes_l[byte_i], i % 8)
                            # format body of frame with bits
                            tx_body += struct.pack('BB', rx_bd_fc, len(bytes_l))
                            # add bytes with bits
                            for byte in bytes_l:
                                tx_body += struct.pack('B', byte)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                # functions Read Holding Registers (0x03) or Read Input Registers (0x04)
                elif rx_bd_fc in (READ_HOLDING_REGISTERS, READ_INPUT_REGISTERS):
                    (w_address, w_count) = struct.unpack('>HH', rx_body[1:])
                    # check quantity of requested words
                    if 0x0001 <= w_count <= 0x007D:
                        if rx_bd_fc == READ_HOLDING_REGISTERS:
                            ret = self.server.data_hdl.read_h_regs(w_address, w_count)
                        else:
                            ret = self.server.data_hdl.read_i_regs(w_address, w_count)
                        if ret.ok:
                            # format body of frame with words
                            tx_body += struct.pack('BB', rx_bd_fc, w_count * 2)
                            for word in ret.data:
                                tx_body += struct.pack('>H', word)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                # function Write Single Coil (0x05)
                elif rx_bd_fc is WRITE_SINGLE_COIL:
                    (b_address, b_value) = struct.unpack('>HH', rx_body[1:])
                    f_b_value = bool(b_value == 0xFF00)
                    ret = self.server.data_hdl.write_coils(b_address, [f_b_value])
                    if ret.ok:
                        # send write ok frame
                        tx_body += struct.pack('>BHH', rx_bd_fc, b_address, b_value)
                    else:
                        exp_status = ret.exp_code
                # function Write Single Register (0x06)
                elif rx_bd_fc is WRITE_SINGLE_REGISTER:
                    (w_address, w_value) = struct.unpack('>HH', rx_body[1:])
                    ret = self.server.data_hdl.write_h_regs(w_address, [w_value])
                    if ret.ok:
                        # send write ok frame
                        tx_body += struct.pack('>BHH', rx_bd_fc, w_address, w_value)
                    else:
                        exp_status = ret.exp_code
                # function Write Multiple Coils (0x0F)
                elif rx_bd_fc is WRITE_MULTIPLE_COILS:
                    (b_address, b_count, byte_count) = struct.unpack('>HHB', rx_body[1:6])
                    # check quantity of updated coils
                    if (0x0001 <= b_count <= 0x07B0) and (byte_count >= (b_count / 8)):
                        # allocate bits list
                        bits_l = [False] * b_count
                        # populate bits list with bits from rx frame
                        for i, item in enumerate(bits_l):
                            b_bit_pos = int(i / 8) + 6
                            b_bit_val = struct.unpack('B', rx_body[b_bit_pos:b_bit_pos + 1])[0]
                            bits_l[i] = test_bit(b_bit_val, i % 8)
                        # write words to data bank
                        ret = self.server.data_hdl.write_coils(b_address, bits_l)
                        if ret.ok:
                            # send write ok frame
                            tx_body += struct.pack('>BHH', rx_bd_fc, b_address, b_count)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                # function Write Multiple Registers (0x10)
                elif rx_bd_fc is WRITE_MULTIPLE_REGISTERS:
                    (w_address, w_count, byte_count) = struct.unpack('>HHB', rx_body[1:6])
                    # check quantity of updated words
                    if (0x0001 <= w_count <= 0x007B) and (byte_count == w_count * 2):
                        # allocate words list
                        words_l = [0] * w_count
                        # populate words list with words from rx frame
                        for i, item in enumerate(words_l):
                            w_offset = i * 2 + 6
                            words_l[i] = struct.unpack('>H', rx_body[w_offset:w_offset + 2])[0]
                        # write words to data bank
                        ret = self.server.data_hdl.write_h_regs(w_address, words_l)
                        if ret.ok:
                            # send write ok frame
                            tx_body += struct.pack('>BHH', rx_bd_fc, w_address, w_count)
                        else:
                            exp_status = ret.exp_code
                    else:
                        exp_status = EXP_DATA_VALUE
                else:
                    exp_status = EXP_ILLEGAL_FUNCTION
                # check exception
                if exp_status != EXP_NONE:
                    # format body of frame with exception status
                    tx_body += struct.pack('BB', rx_bd_fc + 0x80, exp_status)
                # build frame header
                tx_head = struct.pack('>HHHB', rx_hd_tr_id, rx_hd_pr_id, len(tx_body) + 1, rx_hd_unit_id)
                # send frame
                self.request.send(tx_head + tx_body)
            self.request.close()

    def __init__(self, host='localhost', port=MODBUS_PORT, no_block=False, ipv6=False, data_bank=None,
                 data_handler=None):
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
        :param data_handler: a reference to custom ModbusServerDataHandler
        :type data_handler: ModbusServerDataHandler
        """
        # public
        self.host = host
        self.port = port
        self.no_block = no_block
        self.ipv6 = ipv6
        # default data handler is ModbusServerDataHandler or a child of it
        if data_handler is None:
            self.data_hdl = ModbusServerDataHandler(data_bank=data_bank)
        elif isinstance(data_handler, ModbusServerDataHandler):
            if data_handler:
                data_handler.data_bank = data_bank
            self.data_hdl = data_handler
        else:
            raise ValueError('data_handler is invalid')
        # data bank shortcut
        self.data_bank = self.data_hdl.data_bank
        # private
        self._running = False
        self._service = None
        self._serve_th = None

    def start(self):
        """Start the server.

        Do nothing if server is already running.
        This function will block if no_block is not set to True.
        """
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
