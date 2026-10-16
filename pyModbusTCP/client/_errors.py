""" pyModbusTCP client: internal exceptions (never raised to the user, also available as ModbusClient._xxx). """


class InternalError(Exception):
    """ Base of the internal errors of a request (turned into last_error by the client). """


class NetworkError(InternalError):
    """ Network or frame error: code is a MB_xxx_ERR constant. """

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ModbusExcept(InternalError):
    """ Modbus exception returned by the server: code is an EXP_xxx constant. """

    def __init__(self, code: int):
        super().__init__('modbus exception 0x%02X' % code)
        self.code = code
