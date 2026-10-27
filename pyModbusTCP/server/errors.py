""" pyModbusTCP server: exceptions.

Two families:

- Error and its subclasses (also available as ModbusServer.Error, ModbusServer.NetworkError...): errors of the server
  itself (network, frame format). They close the client session.
- ModbusException and its subclasses: a Modbus exception response to send to the client (illegal data address...).
  Raise them from a data store, a store wrapper or a function handler: the server answers the request with the
  exception code, and the session goes on.
"""

from __future__ import annotations

from ..constants import (
    EXP_DATA_ADDRESS,
    EXP_DATA_VALUE,
    EXP_GATEWAY_PATH_UNAVAILABLE,
    EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND,
    EXP_ILLEGAL_FUNCTION,
    EXP_SLAVE_DEVICE_BUSY,
    EXP_SLAVE_DEVICE_FAILURE,
    EXP_TXT,
)


class Error(Exception):
    """ Base exception for ModbusServer related errors. """


class NetworkError(Error):
    """ Exception raised by ModbusServer on I/O errors. """


class DataFormatError(Error):
    """ Exception raised by ModbusServer for data format errors. """


class PDUDecodeError(DataFormatError):
    """ Exception raised when a received request PDU can't be decoded (too short...). """


class ModbusException(Exception):
    """A Modbus exception response: the server answers the request with this exception code.

    Use a subclass (IllegalDataAddress...) for the standard codes, or this class with any code (1 to 255).
    """

    # the exception code of the subclasses
    code: int = EXP_SLAVE_DEVICE_FAILURE

    def __init__(self, message: str = '', code: int | None = None) -> None:
        if code is not None:
            if isinstance(code, bool) or not isinstance(code, int) or not 1 <= code <= 0xFF:
                raise ValueError('a Modbus exception code is an int between 1 and 255')
            self.code = code
        super().__init__(message or EXP_TXT.get(self.code, f'exception 0x{self.code:02X}'))


class IllegalFunction(ModbusException):
    """Exception 0x01: the function is not supported, or not allowed (like a write on a read-only server)."""
    code = EXP_ILLEGAL_FUNCTION


class IllegalDataAddress(ModbusException):
    """Exception 0x02: an address of the request is out of the data space (or not allowed)."""
    code = EXP_DATA_ADDRESS


class IllegalDataValue(ModbusException):
    """Exception 0x03: a value of the request is not accepted."""
    code = EXP_DATA_VALUE


class SlaveDeviceFailure(ModbusException):
    """Exception 0x04: the server failed to process a valid request."""
    code = EXP_SLAVE_DEVICE_FAILURE


class SlaveDeviceBusy(ModbusException):
    """Exception 0x06: the server is busy, the client should retry later."""
    code = EXP_SLAVE_DEVICE_BUSY


class GatewayPathUnavailable(ModbusException):
    """Exception 0x0A: a gateway can't route the request (like an unknown unit id)."""
    code = EXP_GATEWAY_PATH_UNAVAILABLE


class GatewayTargetFailed(ModbusException):
    """Exception 0x0B: the target device behind a gateway didn't answer."""
    code = EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND
