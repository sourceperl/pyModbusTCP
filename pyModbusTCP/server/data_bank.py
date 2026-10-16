""" pyModbusTCP server: DataBank, the default thread safe Modbus data space. """

from __future__ import annotations

from threading import Lock

from ..utils import _to_bool_list, _to_int_list
from .session import ServerInfo


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
                  srv_info: ServerInfo | None = None) -> list[bool] | None:
        """Read data on server coils space

        :param address: start address
        :type address: int
        :param number: number of bits (optional)
        :type number: int
        :param srv_info: some server info (must be set by server only)
        :type srv_info: ServerInfo
        :returns: list of bool or None if error
        :rtype: list or None
        """
        # secure extract of data from list used by server thread
        with self._coils_lock:
            if (address >= 0) and (address + number <= len(self._coils)):
                return self._coils[address: number + address]
            else:
                return None

    def set_coils(self, address: int, bit_list: list[bool],
                  srv_info: ServerInfo | None = None) -> bool | None:
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
                            srv_info: ServerInfo | None = None) -> list[bool] | None:
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

    def set_discrete_inputs(self, address: int, bit_list: list[bool]) -> bool | None:
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
                              srv_info: ServerInfo | None = None) -> list[int] | None:
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

    def set_holding_registers(self, address: int, word_list: list[int],
                              srv_info: ServerInfo | None = None) -> bool | None:
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
                self.on_holding_registers_change(address, from_value, to_value, srv_info)
        return True

    def get_input_registers(self, address: int, number: int = 1,
                            srv_info: ServerInfo | None = None) -> list[int] | None:
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

    def set_input_registers(self, address: int, word_list: list[int]) -> bool | None:
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
                        srv_info: ServerInfo) -> None:
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

    def on_holding_registers_change(self, address: int, from_value: int, to_value: int,
                                    srv_info: ServerInfo) -> None:
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
