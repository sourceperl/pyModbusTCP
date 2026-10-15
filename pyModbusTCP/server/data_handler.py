""" pyModbusTCP server: DataHandler, the interface between the server engine and the data bank. """

from __future__ import annotations

from typing import Any, List, Optional

from ..constants import EXP_DATA_ADDRESS, EXP_NONE
from .data_bank import DataBank
from .session import ServerInfo


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

    def read_coils(self, address: int, count: int, srv_info: ServerInfo) -> DataHandler.Return:
        """Call by server for reading in coils space

        :param address: start address
        :type address: int
        :param count: number of coils
        :type count: int
        :param srv_info: some server info
        :type srv_info: ServerInfo
        :rtype: Return
        """
        # read bits from DataBank
        bits_l = self.data_bank.get_coils(address, count, srv_info)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def write_coils(self, address: int, bits_l: List[bool], srv_info: ServerInfo) -> DataHandler.Return:
        """Call by server for writing in the coils space

        :param address: start address
        :type address: int
        :param bits_l: list of boolean to write
        :type bits_l: list
        :param srv_info: some server info
        :type srv_info: ServerInfo
        :rtype: Return
        """
        # write bits to DataBank
        update_ok = self.data_bank.set_coils(address, bits_l, srv_info)
        # return DataStatus to server
        if update_ok:
            return DataHandler.Return(exp_code=EXP_NONE)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def read_d_inputs(self, address: int, count: int, srv_info: ServerInfo) -> DataHandler.Return:
        """Call by server for reading in the discrete inputs space

        :param address: start address
        :type address: int
        :param count: number of discrete inputs
        :type count: int
        :param srv_info: some server info
        :type srv_info: ServerInfo
        :rtype: Return
        """
        # read bits from DataBank
        bits_l = self.data_bank.get_discrete_inputs(address, count, srv_info)
        # return DataStatus to server
        if bits_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=bits_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def read_h_regs(self, address: int, count: int, srv_info: ServerInfo) -> DataHandler.Return:
        """Call by server for reading in the holding registers space

        :param address: start address
        :type address: int
        :param count: number of holding registers
        :type count: int
        :param srv_info: some server info
        :type srv_info: ServerInfo
        :rtype: Return
        """
        # read words from DataBank
        words_l = self.data_bank.get_holding_registers(address, count, srv_info)
        # return DataStatus to server
        if words_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def write_h_regs(self, address: int, words_l: List[int], srv_info: ServerInfo) -> DataHandler.Return:
        """Call by server for writing in the holding registers space

        :param address: start address
        :type address: int
        :param words_l: list of word value to write
        :type words_l: list
        :param srv_info: some server info
        :type srv_info: ServerInfo
        :rtype: Return
        """
        # write words to DataBank
        update_ok = self.data_bank.set_holding_registers(address, words_l, srv_info)
        # return DataStatus to server
        if update_ok:
            return DataHandler.Return(exp_code=EXP_NONE)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)

    def read_i_regs(self, address: int, count: int, srv_info: ServerInfo) -> DataHandler.Return:
        """Call by server for reading in the input registers space

        :param address: start address
        :type address: int
        :param count: number of input registers
        :type count: int
        :param srv_info: some server info
        :type srv_info: ServerInfo
        :rtype: Return
        """
        # read words from DataBank
        words_l = self.data_bank.get_input_registers(address, count, srv_info)
        # return DataStatus to server
        if words_l is not None:
            return DataHandler.Return(exp_code=EXP_NONE, data=words_l)
        else:
            return DataHandler.Return(exp_code=EXP_DATA_ADDRESS)
