""" pyModbusTCP server: the ModbusServer class (configuration, start/stop and request engine). """

from __future__ import annotations

import socket
import warnings
from collections.abc import Callable, Iterable, Mapping
from threading import RLock, Thread, current_thread
from typing import TypeAlias

from ..constants import (
    ENCAPSULATED_INTERFACE_TRANSPORT,
    EXP_DATA_VALUE,
    EXP_GATEWAY_PATH_UNAVAILABLE,
    EXP_ILLEGAL_FUNCTION,
    EXP_SLAVE_DEVICE_FAILURE,
    MAX_PDU_SIZE,
    MEI_TYPE_READ_DEVICE_ID,
    READ_FUNCTION_CODES,
    WRITE_FUNCTION_CODES,
)
from . import _transport, _validators, errors, frames, session
from ._common import HOST_IPV6_PREFIX, logger
from ._transport import CustomThreadingTCPServer
from .device_id import DeviceIdentification
from .errors import (
    DataFormatError,
    Error,
    ModbusException,
    NetworkError,
    PDUDecodeError,
)
from .functions import FunctionHandler, Request
from .store import DataStore, RequestContext


class ModbusServer:
    """ Modbus TCP server """

    # interval (in s) at which the main server loop checks for a stop request: this is the max latency of stop()
    _SERVE_POLL_INTERVAL: float = 0.05

    # historical nested names (ModbusServer.Error, ModbusServer.SessionData...): the classes live in submodules
    Error: TypeAlias = errors.Error
    NetworkError: TypeAlias = errors.NetworkError
    DataFormatError: TypeAlias = errors.DataFormatError
    PDUDecodeError: TypeAlias = errors.PDUDecodeError
    ClientInfo: TypeAlias = session.ClientInfo
    SessionData: TypeAlias = session.SessionData
    Frame: TypeAlias = frames.Frame
    MBAP: TypeAlias = frames.MBAP
    PDU: TypeAlias = frames.PDU
    CustomThreadingTCPServer: TypeAlias = _transport.CustomThreadingTCPServer
    ModbusRequestHandler: TypeAlias = _transport.ModbusRequestHandler

    # kept for compatibility (allowed hosts check used outside of this class)
    _validate_allowed_hosts = staticmethod(_validators.validate_allowed_hosts)

    def __init__(self, host: str = 'localhost', port: int = 502, no_block: bool = False, *, ipv6: bool = False,
                 store: DataStore | Mapping[int, DataStore] | None = None,
                 functions: Mapping[int, FunctionHandler] | None = None, fallback: FunctionHandler | None = None,
                 device_id: DeviceIdentification | None = None,
                 request_timeout: float | None = 30.0, idle_timeout: float | None = 300,
                 max_connections: int | None = 64, max_connections_per_host: int | None = 16,
                 read_only: bool = False, allowed_hosts: Iterable[str] | None = None,
                 ipv6_host_prefix: int = HOST_IPV6_PREFIX) -> None:
        """Initialize a Modbus TCP server.

        Sets up a Modbus TCP server with configurable network parameters, data handling,
        and optional device identification. Type validation is performed on critical
        parameters to prevent silent runtime failures.

        The arguments after no_block are keyword-only (like ``ModbusServer('0.0.0.0', 5020, read_only=True)``).

        The access control and limit settings (allowed_hosts, read_only, max_connections, max_connections_per_host,
        ipv6_host_prefix, request_timeout and idle_timeout) are also properties: a new value is checked like in the
        constructor and applies at once to a running server (see each property). host, port and ipv6 apply at the
        next start().

        :param host: hostname or IPv4/IPv6 address to listen on (default is 'localhost'), '0.0.0.0' (or '::' with\
            ipv6=True) for every interface; an empty host, or an IPv4 address in a legacy form that bind() reads as\
            another address ('0' is 0.0.0.0, '192.168.010.1' is 192.168.8.1), is refused (ValueError); a link-local\
            IPv6 address with its interface only ('fe80::1%eth0': the server only listens on this link)
        :type host: str
        :param port: TCP port number (default is 502), use 0 to let the OS choose a free port
        :type port: int
        :param no_block: no block mode, i.e. start() will return (default is False)
        :type no_block: bool
        :param ipv6: use ipv6 stack (default is False)
        :type ipv6: bool
        :param store: the data store of the server (default: a new MemoryStore), or a mapping {unit id: store} to\
            simulate several devices (a request for another unit id gets a "gateway path unavailable" exception)
        :type store: DataStore or dict or None
        :param functions: the function handlers, {function code: handler(request) -> response PDU} (default: a copy\
            of the standard functions, see STANDARD_FUNCTIONS), also available as the functions attribute
        :type functions: dict or None
        :param fallback: the handler of the requests whose function code has no handler (default: None, an\
            "illegal function" exception), like a gateway that relays every request
        :type fallback: callable or None
        :param device_id: instance of DeviceIdentification class for read device identification request (optional)
        :type device_id: DeviceIdentification
        :param request_timeout: max time in seconds to receive a whole request (MBAP header and PDU) from its first\
            byte (a stalled request), on expiry the client session is closed, None to disable (default is 30.0)
        :type request_timeout: float or None
        :param idle_timeout: max time in seconds without any request from a client before closing its session,\
            None to disable (default is 300.0)
        :type idle_timeout: float or None
        :param max_connections: max number of simultaneous client connections, the next ones are closed immediately,\
            None for no limit (default is 64)
        :type max_connections: int or None
        :param read_only: refuse the standard write functions (0x05, 0x06, 0x0F, 0x10, 0x15, 0x16 and 0x17) with an\
            "illegal function" exception, whatever their handler; the fallback handler only gets the read functions\
            (0x01 to 0x04, 0x2B/0x0E) (default is False). A function added or replaced by the application (see\
            function()) is not checked: if it writes data, it must test server.read_only itself (and raise\
            IllegalFunction), or the store must be wrapped in a ReadOnly wrapper
        :type read_only: bool
        :param allowed_hosts: whitelist of the clients allowed to connect: IP addresses or networks in CIDR notation\
            (like ['192.168.1.10', '10.0.0.0/24', '::1']), other connections are closed at once, None to accept\
            every client (default), an empty list for no client at all, hostnames are not supported; an IPv4-mapped\
            entry (::ffff:a.b.c.d) is converted to IPv4, a link-local entry (fe80::/10) is refused (an allow list\
            can't tell the interface of a client)
        :type allowed_hosts: iterable of str or None
        :param max_connections_per_host: max number of simultaneous connections from the same client host (an IPv4\
            address, or an IPv6 network, see ipv6_host_prefix), the next ones are closed immediately: a single host\
            can't take all the connections of max_connections. Clients behind a NAT share the same address. None for\
            no limit (default is 16)
        :type max_connections_per_host: int or None
        :param ipv6_host_prefix: prefix length of the IPv6 network counted as one host by max_connections_per_host,\
            1 to 128 (default is 64: a host usually gets a whole /64 and can use any address of it, so a smaller\
            group is easy to bypass). 128 counts each address apart: only for a network where a host can't choose\
            its addresses (static addresses, no SLAAC, source address filtering on the switches), or with\
            allowed_hosts. A larger group (like 56) counts a client with a whole /56 as one host
        :type ipv6_host_prefix: int
        """
        # validate network parameters (a bad value would only be seen by start())
        host = _validators.validate_host(host)
        port = _validators.validate_port(port)

        # validate complex objects (data storage and function handlers)
        stores = _validators.validate_store(store)
        checked_functions = _validators.validate_functions(functions)
        fallback = _validators.validate_fallback(fallback)
        device_id = _validators.validate_device_id(device_id)

        # ensure atomic start and stop operations, and a setting change never lost by a concurrent start()
        # (reentrant: stop() may be called from a signal handler running in the thread that is inside start())
        self._start_stop_lock = RLock()
        # the TCP server of a running server (None when stopped): the settings below are passed to it by start(), and
        # their setters update it at once (set first, the setters read it)
        self._tcp_server: CustomThreadingTCPServer | None = None

        # store network configuration (applied by the next start())
        self.host = host
        self.port = port
        self.no_block = no_block
        self.ipv6 = ipv6

        # access control and limits: properties, checked by their setters and applied at once to a running server
        # (validated in this order: a constructor with a bad value raises before anything else is changed)
        self.request_timeout = request_timeout
        self.idle_timeout = idle_timeout
        self.max_connections = max_connections
        self.max_connections_per_host = max_connections_per_host
        self.ipv6_host_prefix = ipv6_host_prefix
        self.read_only = read_only
        self.allowed_hosts = allowed_hosts

        # data stores: unit id -> store (the key None: the store of every unit id)
        self._stores = stores
        #: unit id -> data store, when the server has one store per unit id (else empty)
        self.stores: dict[int, DataStore] = {unit_id: unit_store for unit_id, unit_store in stores.items()
                                             if unit_id is not None}
        #: function code -> handler, can be changed at any time (see function())
        self.functions: dict[int, FunctionHandler] = checked_functions
        #: handler of the other function codes (None: "illegal function" exception)
        self.fallback = fallback
        self.device_id = device_id

        # initialize internal state for server operation
        # server thread handle
        self._srv_thread: Thread | None = None

    @property
    def store(self) -> DataStore:
        """The data store of the server (application access: server.store.app).

        :raises AttributeError: if the server has one store per unit id (see stores)
        """
        store = self._stores.get(None)
        if store is None:
            raise AttributeError('this server has one store per unit id: use server.stores[unit_id]')
        return store

    @property
    def host(self) -> str:
        """Get or set the address to listen on (applied by the next start()).

        An empty host is refused (ValueError): for bind(), it is every interface. Use '0.0.0.0' (or '::' with
        ipv6=True) for that. So is an IPv4 address in a legacy form, read by bind() as another address ('0', '127.1',
        '192.168.010.1'...).
        """
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        self._host = _validators.validate_host(value)

    # --- access control and limits: checked, and applied at once to a running server ---

    @property
    def allowed_hosts(self) -> tuple[str, ...] | None:
        """Get or set the whitelist of the clients allowed to connect, as normalized networks (CIDR notation).

        None: every client, an empty tuple: no client at all. A new value (an iterable of IP addresses or networks,
        or None) is checked like the constructor argument. On a running server it applies at once: the next
        connections are filtered with it, and the active sessions of the clients it no longer allows are closed.
        The value is a tuple: change the whitelist by setting a new value, never in place.
        """
        networks = self._allowed_networks
        return None if networks is None else tuple(str(network) for network in networks)

    @allowed_hosts.setter
    def allowed_hosts(self, value: Iterable[str] | None) -> None:
        networks = _validators.validate_allowed_hosts(value)
        with self._start_stop_lock:
            self._allowed_networks = networks
            tcp_server = self._tcp_server
            if tcp_server is not None:
                tcp_server.allowed_networks = networks
                closed = tcp_server.shutdown_disallowed_sessions()
                if closed:
                    logger.info('allowed_hosts changed: %d session(s) of clients no longer allowed closed', closed)
                if networks == ():
                    logger.warning('allowed_hosts is an empty list: every connection will be refused')

    @property
    def read_only(self) -> bool:
        """Get or set the read-only mode (see the constructor): applies from the next request.

        Only the standard write functions are refused: a function handler of the application that writes data tests
        it at each call (``if server.read_only: raise IllegalFunction()``).
        """
        return self._read_only

    @read_only.setter
    def read_only(self, value: bool) -> None:
        self._read_only = _validators.validate_read_only(value)

    @property
    def max_connections(self) -> int | None:
        """Get or set the max number of simultaneous client connections (None: no limit).

        On a running server, it applies to the next connections (the active sessions are kept).
        """
        return self._max_connections

    @max_connections.setter
    def max_connections(self, value: int | None) -> None:
        value = _validators.validate_max_connections(value)
        with self._start_stop_lock:
            self._max_connections = value
            if self._tcp_server is not None:
                self._tcp_server.max_connections = value

    @property
    def max_connections_per_host(self) -> int | None:
        """Get or set the max number of simultaneous connections of a client host (None: no limit).

        On a running server, it applies to the next connections (the active sessions are kept).
        """
        return self._max_connections_per_host

    @max_connections_per_host.setter
    def max_connections_per_host(self, value: int | None) -> None:
        value = _validators.validate_max_connections(value, 'max_connections_per_host')
        with self._start_stop_lock:
            self._max_connections_per_host = value
            if self._tcp_server is not None:
                self._tcp_server.max_connections_per_host = value

    @property
    def ipv6_host_prefix(self) -> int:
        """Get or set the prefix length of the IPv6 network counted as one host by max_connections_per_host.

        64 by default, 128 to count each address apart (see the constructor). On a running server, the active
        sessions are counted again with the new value, which applies to the next connections (no session is closed).
        """
        return self._ipv6_host_prefix

    @ipv6_host_prefix.setter
    def ipv6_host_prefix(self, value: int) -> None:
        value = _validators.validate_ipv6_host_prefix(value)
        with self._start_stop_lock:
            self._ipv6_host_prefix = value
            if self._tcp_server is not None:
                self._tcp_server.ipv6_host_prefix = value

    @property
    def request_timeout(self) -> float | None:
        """Get or set the max time in seconds to receive a whole request (None: no limit).

        On a running server, it applies from the next request of each session.
        """
        return self._request_timeout

    @request_timeout.setter
    def request_timeout(self, value: float | None) -> None:
        value = _validators.validate_timeout(value, 'request_timeout')
        with self._start_stop_lock:
            self._request_timeout = value
            if self._tcp_server is not None:
                self._tcp_server.request_timeout = value

    @property
    def idle_timeout(self) -> float | None:
        """Get or set the max time in seconds without any request before a session is closed (None: no limit).

        On a running server, it applies from the next wait for a request of each session.
        """
        return self._idle_timeout

    @idle_timeout.setter
    def idle_timeout(self, value: float | None) -> None:
        value = _validators.validate_timeout(value, 'idle_timeout')
        with self._start_stop_lock:
            self._idle_timeout = value
            if self._tcp_server is not None:
                self._tcp_server.idle_timeout = value

    @property
    def is_run(self) -> bool:
        """Deprecated: use is_running."""
        warnings.warn('ModbusServer.is_run is deprecated, use is_running instead',
                      DeprecationWarning, stacklevel=2)
        return self.is_running

    @property
    def is_running(self) -> bool:
        """Return True if server running."""
        return self._srv_thread is not None and self._srv_thread.is_alive()

    @property
    def active_sessions(self) -> list[tuple[str, int]]:
        """Return the (address, port) of the clients of all active sessions (empty if server is stopped)."""
        tcp_server = self._tcp_server
        if tcp_server is None:
            return []
        return [tuple(client_address[:2]) for _, client_address in tcp_server.active_sessions()]

    def shutdown_sessions(self) -> int:
        """Call shutdown() on every active session (client connection).

        The server keeps listening: new clients can still connect afterwards.
        It is automatically called when the server stops.

        :return: number of sessions the shutdown was requested for
        :rtype: int
        """
        tcp_server = self._tcp_server
        if tcp_server is None:
            return 0
        return tcp_server.shutdown_sessions()

    def __repr__(self) -> str:
        r_str = ('ModbusServer(host=\'%s\', port=%d, no_block=%s, ipv6=%s, store=%r, functions=%s, fallback=%r, '
                 'device_id=%s, request_timeout=%s, idle_timeout=%s, max_connections=%s, max_connections_per_host=%s, '
                 'ipv6_host_prefix=%d, read_only=%s, allowed_hosts=%s)')
        r_str %= (self.host, self.port, self.no_block, self.ipv6, self._stores.get(None, self.stores),
                  sorted(self.functions), self.fallback, self.device_id, self.request_timeout, self.idle_timeout,
                  self.max_connections, self.max_connections_per_host, self.ipv6_host_prefix, self.read_only,
                  None if self.allowed_hosts is None else list(self.allowed_hosts))
        return r_str

    def function(self, code: int) -> Callable[[FunctionHandler], FunctionHandler]:
        """Decorator to add (or replace) the handler of a function code::

            @server.function(0x41)
            def my_function(req: Request) -> bytes:
                return req.reply('B', 42)

        A handler takes a Request and returns the response PDU (bytes, function code first). It raises a
        ModbusException (like IllegalDataAddress) for an exception response. Delete server.functions[code] to remove
        a function.
        """
        code = _validators.validate_function_code(code)

        def register(handler: FunctionHandler) -> FunctionHandler:
            if not callable(handler):
                raise TypeError('a function handler must be callable')
            self.functions[code] = handler
            return handler

        return register

    def _engine(self, session_data: session.SessionData) -> None:
        """Process a request: route it to its function handler, set the response PDU.

        :type session_data: SessionData
        """
        client = session_data.client
        request = session_data.request
        send_pdu = session_data.response.pdu
        func_code = request.pdu.func_code
        # a request can't have the exception flag (bit 7) set, and an exception response must fit in a byte:
        # answer "illegal function" with the same code (code | 0x80), the TCP session stays synchronized
        if func_code > 0x7F:
            logger.debug('Request with an invalid function code 0x%02X from %s', func_code, client)
            send_pdu.build_except(func_code & 0x7F, EXP_ILLEGAL_FUNCTION)
            return
        unit_id = request.mbap.unit_id
        routed = self._route(request.pdu.raw, unit_id, client)
        if isinstance(routed, int):
            send_pdu.build_except(func_code, routed)
            return
        handler, store = routed
        ctx = RequestContext(client=client, unit_id=unit_id, function_code=func_code)
        try:
            response = handler(Request(pdu=request.pdu.raw, ctx=ctx, store=store, device_id=self.device_id))
            send_pdu.raw = _checked_response(response)
        except ModbusException as e:
            logger.debug('Function 0x%02X from %s: exception 0x%02X (%s)', func_code, client, e.code, e)
            send_pdu.build_except(func_code, e.code)
        except PDUDecodeError as e:
            # request PDU too short or malformed: the TCP stream is still in sync, answer with "illegal data value"
            logger.debug('Malformed request for function 0x%02X: %s from %s', func_code, e, client)
            send_pdu.build_except(func_code, EXP_DATA_VALUE)
        except DataFormatError as e:
            # the response can't be encoded (like a value out of its field), the request itself was fine and the TCP
            # stream is still in sync: answer with "slave device failure" instead of closing the session
            logger.error('Unable to encode the response of function 0x%02X from %s: %s', func_code, client, e)
            send_pdu.build_except(func_code, EXP_SLAVE_DEVICE_FAILURE)
        except Error:
            # network error or other frame error: let the session handler close the connection
            raise
        except Exception:  # noqa: BLE001
            # unexpected error (like an exception raised by user code): log it and keep the session alive
            logger.exception('Unexpected error during processing of function 0x%02X from %s', func_code, client)
            send_pdu.build_except(func_code, EXP_SLAVE_DEVICE_FAILURE)

    def _route(self, pdu: bytes, unit_id: int, client: session.ClientInfo) -> tuple[FunctionHandler, DataStore] | int:
        """Return the handler and the store of a request, or the exception code to answer."""
        func_code = pdu[0]
        # read-only server: a function that writes data is not available, and the fallback only gets the read
        # functions (it may relay any function: 0x08 diagnostics, a private function... can write)
        if self.read_only:
            relayed = func_code not in self.functions
            is_read = func_code in READ_FUNCTION_CODES and (func_code != ENCAPSULATED_INTERFACE_TRANSPORT
                                                            or pdu[1:2] == bytes([MEI_TYPE_READ_DEVICE_ID]))
            if func_code in WRITE_FUNCTION_CODES or (relayed and not is_read):
                logger.debug('Function 0x%02X refused (read-only mode) from %s', func_code, client)
                return EXP_ILLEGAL_FUNCTION
        # the store of the unit id
        store = self._stores.get(unit_id, self._stores.get(None))
        if store is None:
            logger.debug('Request for the unknown unit id %d from %s', unit_id, client)
            return EXP_GATEWAY_PATH_UNAVAILABLE
        # the handler of the function code, or the fallback
        handler = self.functions.get(func_code, self.fallback)
        if handler is None:
            return EXP_ILLEGAL_FUNCTION
        return handler, store

    def start(self) -> tuple[str, int]:
        """Start the server.

        This function will block (or not if no_block flag is set).
        With port=0, the port chosen by the OS is available through the returned tuple.

        :return: A tuple containing the bound host string and port integer.
        :rtype: Tuple[str, int]
        :raises NetworkError: if the server can't listen (port already in use, bad host...)
        """
        with self._start_stop_lock:
            # do nothing if server is already running
            if self.is_running:
                raise NetworkError('server is already running')
            # init TCP server
            # always need a fresh instance since server_close() destroys the underlying socket file descriptor
            tcp_server: CustomThreadingTCPServer | None = None
            try:
                # IPv4 or IPv6: handled by the server class, the socket is created in its constructor
                tcp_server = self.CustomThreadingTCPServer((self.host, self.port), self.ModbusRequestHandler,
                                                           bind_and_activate=False, ipv6=self.ipv6)
                # pass some things shared with server threads (access via self.server in ModbusRequestHandler.handle())
                tcp_server.engine = self._engine
                # (under the start/stop lock: a setter can't change one of them between this copy and the publication
                # of tcp_server in self._tcp_server below, from where the setters update it)
                tcp_server.request_timeout = self.request_timeout
                tcp_server.idle_timeout = self.idle_timeout
                tcp_server.max_connections = self.max_connections
                tcp_server.max_connections_per_host = self.max_connections_per_host
                tcp_server.ipv6_host_prefix = self.ipv6_host_prefix
                tcp_server.allowed_networks = self._allowed_networks
                # set socket options
                tcp_server.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                # bind and activate
                tcp_server.server_bind()
                tcp_server.server_activate()
            except BaseException as e:
                # don't keep the listening socket open after a failed start (OSError, or a TypeError/OverflowError
                # from bind() for an invalid host or port set after the constructor)
                if tcp_server is not None:
                    tcp_server.server_close()
                if isinstance(e, OSError):
                    raise NetworkError(e) from e
                raise
            logger.info('Listening on %s:%d', *tcp_server.server_address[:2])
            if self._allowed_networks == ():
                logger.warning('allowed_hosts is an empty list: every connection will be refused')
            self._tcp_server = tcp_server

            # start the request processing service
            srv_thread = Thread(target=self._serve, args=(tcp_server,), daemon=True)
            self._srv_thread = srv_thread
            try:
                self._srv_thread.start()
            except BaseException:
                # unable to start thread
                tcp_server.server_close()
                self._tcp_server = None
                self._srv_thread = None
                raise
        # block here if we are not in no_block mode
        if not self.no_block:
            srv_thread.join()
        # return current bindings to client
        host, port = tcp_server.server_address[:2]
        return str(host), int(port)

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the server thread to finish (useful in non-blocking mode)."""
        srv_thread = self._srv_thread
        if srv_thread is not None:
            srv_thread.join(timeout=timeout)

    def stop(self) -> None:
        """Stop the server."""
        with self._start_stop_lock:
            # send a shutdown request to TCP server (tell the serve_forever() loop to stop and wait until it does)
            tcp_server = self._tcp_server
            if tcp_server is not None:
                tcp_server.shutdown()
            # wait for the server thread: a start() right after stop() must find a clean state
            srv_thread = self._srv_thread
            if srv_thread is not None and srv_thread is not current_thread():
                srv_thread.join()
            self._srv_thread = None
            self._tcp_server = None

    def _serve(self, tcp_server: CustomThreadingTCPServer) -> None:
        # the server is given as argument: self._tcp_server may be reset by a concurrent stop() at any time
        try:
            tcp_server.serve_forever(poll_interval=self._SERVE_POLL_INTERVAL)
        finally:
            # actively shutdown every session instead of waiting for them to notice the flag
            tcp_server.shutdown_sessions()
            tcp_server.server_close()


def _checked_response(response: object) -> bytes:
    """The response PDU of a handler: non empty bytes, at most MAX_PDU_SIZE (253) bytes.

    :raises DataFormatError: for another response (the client gets a "slave device failure")
    """
    if not isinstance(response, (bytes, bytearray)) or not response:
        raise DataFormatError(f'the handler returned {type(response).__name__}, a non empty bytes expected')
    if len(response) > MAX_PDU_SIZE:
        raise DataFormatError(f'the handler returned a PDU of {len(response)} bytes (max {MAX_PDU_SIZE})')
    return bytes(response)
