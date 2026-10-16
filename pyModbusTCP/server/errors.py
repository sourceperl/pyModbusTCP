""" pyModbusTCP server: exceptions (also available as ModbusServer.Error, ModbusServer.NetworkError...). """

from __future__ import annotations


class Error(Exception):
    """ Base exception for ModbusServer related errors. """


class NetworkError(Error):
    """ Exception raise by ModbusServer on I/O errors. """


class DataFormatError(Error):
    """ Exception raise by ModbusServer for data format errors. """


class PDUDecodeError(DataFormatError):
    """ Exception raise when a received request PDU can't be decoded (too short...). """
