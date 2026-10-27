""" pyModbusTCP client: internal exceptions (never raised to the user, also available as ModbusClient._xxx). """


class InternalError(Exception):
    """ Base of the internal errors of a request (turned into last_error by the client). """


class NetworkError(InternalError):
    """ Network or frame error: code is a MB_xxx_ERR constant. """

    def __init__(self, code: int, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        # True if a new attempt is safe and may succeed: a connect error (but not an unknown host name), or a request
        # whose send failed on a connection already reset (it never reached the server)
        self.retryable = retryable


class ModbusExcept(InternalError):
    """ Modbus exception returned by the server: code is an EXP_xxx constant. """

    def __init__(self, code: int):
        super().__init__(f'modbus exception 0x{code:02X}')
        self.code = code
