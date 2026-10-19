""" pyModbusTCP client: the ModbusClient class (settings, request status and error handling). """

import math
import random
import socket
from types import TracebackType
from typing import TYPE_CHECKING, TypeAlias

from ..constants import (
    EXP_DETAILS,
    EXP_NONE,
    EXP_TXT,
    MB_ERR_TXT,
    MB_EXCEPT_ERR,
    MB_NO_ERR,
    VERSION,
)
from ..utils import _to_bool
from . import _errors, _validators
from ._errors import ModbusExcept, NetworkError
from ._functions import ClientFunctionsMixin
from ._transport import ClientTransportMixin

if TYPE_CHECKING:
    # typing.Self needs Python 3.11: its backport is only used by the type checkers (mypy installs it)
    from typing_extensions import Self


class ModbusClient(ClientFunctionsMixin, ClientTransportMixin):
    """Modbus TCP client.

    An instance is not thread-safe: share it between threads only with an external lock.
    """

    # historical nested names of the internal exceptions: the classes live in the _errors module
    _InternalError: TypeAlias = _errors.InternalError
    _NetworkError: TypeAlias = _errors.NetworkError
    _ModbusExcept: TypeAlias = _errors.ModbusExcept

    def __init__(self, host: str = 'localhost', port: int = 502, unit_id: int = 1, timeout: float = 30.0,
                 auto_open: bool = True, auto_close: bool = False, no_delay: bool = True,
                 broadcast_interval: float = 0.15):
        """Constructor.

        :param host: hostname or IPv4/IPv6 address of the server
        :type host: str
        :param port: TCP port number
        :type port: int
        :param unit_id: unit ID
        :type unit_id: int
        :param timeout: timeout in seconds for a request (send + receive), not for each socket operation
        :type timeout: float
        :param auto_open: auto TCP connect
        :type auto_open: bool
        :param auto_close: auto TCP close
        :type auto_close: bool
        :param no_delay: disable Nagle's algorithm on TCP connection (True by default)
        :type no_delay: bool
        :param broadcast_interval: min interval in seconds between two broadcast requests (default 0.15, 0 for no
            limit): a broadcast sent sooner waits, a client never sends broadcasts faster than a serial line can take
        :type broadcast_interval: float
        :return: Object ModbusClient
        :rtype: ModbusClient
        """
        # private
        # set _sock first: __del__() always needs it, even if a validation below fails
        self._sock: socket.socket | None = None
        self._broadcast_ids: dict[int, float] = {}
        # constructor arguments: validate them
        self._host = _validators.validate_host(host)
        self._port = _validators.validate_port(port)
        self._unit_id = _validators.validate_unit_id(unit_id)
        self._timeout = _validators.validate_timeout(timeout)
        self._auto_open = _to_bool(auto_open, 'auto_open')
        self._auto_close = _to_bool(auto_close, 'auto_close')
        self._no_delay = _to_bool(no_delay, 'no_delay')
        self._broadcast_interval = _validators.validate_broadcast_interval(broadcast_interval)
        # time of the last broadcast request (time.monotonic()), for broadcast_interval
        self._last_broadcast = -math.inf
        # internal variables
        self._deadline = 0.0  # end of the current request (time.monotonic() value), see _send() and _recv()
        # MBAP transaction ID: a counter incremented at each request
        self._transaction_id = random.randint(0, 0xffff)
        self._version = VERSION  # this package version number
        self._last_error = MB_NO_ERR  # last error code
        self._last_except = EXP_NONE  # last except code

    def __repr__(self) -> str:
        r_str = ('ModbusClient(host=\'%s\', port=%d, unit_id=%d, timeout=%.2f, auto_open=%s, auto_close=%s, '
                 'no_delay=%s, broadcast_interval=%g)')
        r_str %= (self.host, self.port, self.unit_id, self.timeout, self.auto_open, self.auto_close, self.no_delay,
                  self.broadcast_interval)
        return r_str

    def __del__(self) -> None:
        # don't call close() if __init__() failed before _sock is set
        if hasattr(self, '_sock'):
            self.close()

    def __enter__(self) -> 'Self':
        """Context manager: the connection is closed on exit (it is opened at first request with auto_open)."""
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()

    @property
    def host(self) -> str:
        """Get or set the server to connect to.

        This can be any string with a valid IPv4 / IPv6 address or hostname. An internationalized domain name
        must be given in its ASCII form (punycode, like ``'xn--caf-dma.fr'`` for ``'café.fr'``), see valid_host().
        Setting host to a new value will close the current socket.
        """
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        # validate
        value = _validators.validate_host(value)
        value_change = self._host != value
        self._host = value
        # on change handler
        if value_change:
            self._on_change_host()

    def _on_change_host(self) -> None:
        self.close()

    @property
    def port(self) -> int:
        """Get or set the current TCP port (default is 502).

        Setting port to a new value will close the current socket.
        """
        return self._port

    @port.setter
    def port(self, value: int) -> None:
        # validate
        value = _validators.validate_port(value)
        value_change = value != self._port
        self._port = value
        # on change handler
        if value_change:
            self._on_change_port()

    def _on_change_port(self) -> None:
        self.close()

    @property
    def unit_id(self) -> int:
        """Get or set the modbus unit identifier (default is 1).

        Any int from 0 to 255 is valid.
        """
        return self._unit_id

    @unit_id.setter
    def unit_id(self, value: int) -> None:
        # validate
        value = _validators.validate_unit_id(value)
        value_change = value != self._unit_id
        self._unit_id = value
        # on change handler
        if value_change:
            self._on_change_unit_id()

    def _on_change_unit_id(self) -> None:
        pass

    @property
    def timeout(self) -> float:
        """Get or set requests timeout (default is 30 seconds).

        The argument may be a floating point number for sub-second precision.
        This is a limit for the whole request (send + receive), applied from the next request on
        (the current connection is kept).
        """
        return self._timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        # validate
        value = _validators.validate_timeout(value)
        value_change = value != self._timeout
        self._timeout = value
        # on change handler
        if value_change:
            self._on_change_timeout()

    def _on_change_timeout(self) -> None:
        pass

    @property
    def auto_open(self) -> bool:
        """Get or set automatic TCP connect mode (True = turn on)."""
        return self._auto_open

    @auto_open.setter
    def auto_open(self, value: bool) -> None:
        self._auto_open = _to_bool(value, 'auto_open')

    @property
    def auto_close(self) -> bool:
        """Get or set automatic TCP close after each request mode (True = turn on)."""
        return self._auto_close

    @auto_close.setter
    def auto_close(self, value: bool) -> None:
        self._auto_close = _to_bool(value, 'auto_close')

    @property
    def broadcast_interval(self) -> float:
        """Get or set the min interval in seconds between two broadcast requests (default 0.15, 0 for no limit)."""
        return self._broadcast_interval

    @broadcast_interval.setter
    def broadcast_interval(self, value: float) -> None:
        self._broadcast_interval = _validators.validate_broadcast_interval(value)

    @property
    def no_delay(self) -> bool:
        """Get or set TCP_NODELAY mode on connection (True = disable Nagle's algorithm, default)."""
        return self._no_delay

    @no_delay.setter
    def no_delay(self, value: bool) -> None:
        self._no_delay = _to_bool(value, 'no_delay')
        # apply it at once on an open connection (like for the other settings, no need to reconnect)
        if self.is_open:
            self._set_sock_tcp_no_delay(value=self.no_delay)

    def open(self) -> bool:
        """Connect to modbus server (open TCP connection).

        :returns: connect status (True on success)
        :rtype: bool
        """
        self._req_init()
        try:
            self._open()
            return True
        except NetworkError as e:
            self._req_except_handler(e)
            return False

    @property
    def version(self) -> str:
        """Return the current package version as a str."""
        return self._version

    @property
    def last_error(self) -> int:
        """Last error code."""
        return self._last_error

    @property
    def last_error_as_txt(self) -> str:
        """Human-readable text that describe last error."""
        return MB_ERR_TXT.get(self._last_error, 'unknown error')

    @property
    def last_except(self) -> int:
        """Return the last modbus exception code."""
        return self._last_except

    @property
    def last_except_as_txt(self) -> str:
        """Short human-readable text that describe last modbus exception."""
        default_str = f'unreferenced exception 0x{self._last_except:X}'
        return EXP_TXT.get(self._last_except, default_str)

    @property
    def last_except_as_full_txt(self) -> str:
        """Verbose human-readable text that describe last modbus exception."""
        default_str = f'unreferenced exception 0x{self._last_except:X}'
        return EXP_DETAILS.get(self._last_except, default_str)

    def _req_pdu(self, tx_pdu: bytes, rx_min_len: int = 2) -> bytes:
        """Request processing (send and recv PDU).

        :param tx_pdu: modbus PDU (protocol data unit) to send
        :type tx_pdu: bytes
        :param rx_min_len: min length of receive PDU
        :type rx_min_len: int
        :returns: the receive PDU
        :rtype: bytes
        """
        # init request engine
        self._req_init()
        # send PDU
        self._send_pdu(tx_pdu)
        # return receive PDU
        return self._recv_pdu(func_code=tx_pdu[0], min_len=rx_min_len)

    def _broadcast_req(self, tx_pdu: bytes) -> bool:
        """Broadcast request: send the PDU to the unit id 0, no reply is expected.

        :returns: True if the request is sent
        """
        self._req_init()
        try:
            self._send_broadcast(tx_pdu)
            return True
        except _errors.InternalError as e:
            self._req_except_handler(e)
            return False

    def _req_init(self) -> None:
        """Reset request status flags."""
        self._last_error = MB_NO_ERR
        self._last_except = EXP_NONE

    def _req_except_handler(self, _except: Exception) -> None:
        """Global handler for internal exceptions."""
        # on request network error
        if isinstance(_except, NetworkError):
            self._last_error = _except.code
            self._debug_msg(_except.message)
        # on request modbus except
        elif isinstance(_except, ModbusExcept):
            self._last_error = MB_EXCEPT_ERR
            self._last_except = _except.code
            self._debug_msg(f'modbus exception (code {self.last_except} "{self.last_except_as_txt}")')
