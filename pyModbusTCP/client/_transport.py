""" pyModbusTCP client: TCP layer (connection, send/receive of the Modbus/TCP frames, frames trace). """

import logging
import socket
import struct
import time
from binascii import hexlify
from socket import AF_UNSPEC, IPPROTO_TCP, SOCK_STREAM, TCP_NODELAY

from ..constants import (
    MB_CONNECT_ERR,
    MB_RECV_ERR,
    MB_SEND_ERR,
    MB_SOCK_CLOSE_ERR,
    MB_TIMEOUT_ERR,
    MBAP_MAX_LENGTH,
    MBAP_MIN_LENGTH,
    MBAP_SIZE,
)
from ._common import logger
from ._errors import ModbusExcept, NetworkError


class ClientTransportMixin:
    """TCP layer of ModbusClient: open/close the connection, send a request PDU and receive the reply PDU.

    The frames are checked here (MBAP header, function code, exception), not their content.
    """

    # set by ModbusClient (settings and connection state)
    host: str
    port: int
    unit_id: int
    timeout: float
    auto_open: bool
    auto_close: bool
    _no_delay: bool
    _sock: socket.socket | None
    _deadline: float
    _transaction_id: int

    @property
    def is_open(self) -> bool:
        """Get current status of the TCP connection (True = open)."""
        if self._sock is None:
            return False
        try:
            return self._sock.fileno() >= 0
        except (AttributeError, OSError):
            return False

    def _open(self) -> None:
        """Connect to modbus server (open TCP connection)."""
        # open an already open socket -> reset it
        if self.is_open:
            self.close()
        # list available sockets on the target host/port
        # AF_xxx : AF_INET -> IPv4, AF_INET6 -> IPv6,
        #          AF_UNSPEC -> IPv6 (priority on some system) or 4
        try:
            addr_infos = socket.getaddrinfo(self.host, self.port, AF_UNSPEC, SOCK_STREAM)
        except socket.gaierror as e:
            # name resolution failure: report it like any other connect error (no exception for the caller)
            raise NetworkError(MB_CONNECT_ERR, 'host name resolution error: %s' % e)
        # try each address in turn, keep the last failure reason for the error message
        last_err: OSError | None = None
        for af, sock_type, proto, _canon_name, sa in addr_infos:
            try:
                self._sock = socket.socket(af, sock_type, proto)
            except OSError as e:
                last_err = e
                continue
            try:
                self._sock.settimeout(self.timeout)
                # disable Nagle's algorithm: modbus requests are small frames
                # that expect an immediate reply (request/response pattern)
                self._set_sock_tcp_no_delay(self._no_delay)
                self._sock.connect(sa)
            except OSError as e:
                last_err = e
                self.close()
                continue
            break
        # check connect status
        if not self.is_open:
            reason = 'connection refused' if last_err is None else 'connect error: %s' % last_err
            raise NetworkError(MB_CONNECT_ERR, reason)

    def close(self) -> None:
        """Close current TCP connection."""
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            finally:
                self._sock = None

    def _set_sock_timeout(self, value: float) -> None:
        """Set the current socket timeout, but only if it differs by more than 10 ms from the current one.

        Each settimeout() is a system call: in the usual case (response received at once) the timeout in place
        is already (almost) the right one, so there is nothing to do.

        :param value: socket timeout in seconds
        :type value: float
        """
        # skip for unset socket
        if self._sock is None:
            return
        # apply timeout if diff is great than 10ms
        current = self._sock.gettimeout()
        if current is None or abs(current - value) > 0.01:
            self._sock.settimeout(value)

    def _set_sock_tcp_no_delay(self, value: bool) -> None:
        # skip for unset socket
        if self._sock is None:
            return
        # apply tcp_nodelay value
        try:
            self._sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, int(value))
        except OSError as e:
            logger.debug('Unable to change TCP_NODELAY on the open connection: %r', e)

    def _send(self, frame: bytes) -> None:
        """Send frame over current socket.

        :param frame: modbus frame to send (MBAP + PDU)
        :type frame: bytes
        """
        # check socket status
        if self._sock is None or not self.is_open:
            raise NetworkError(MB_SOCK_CLOSE_ERR, 'try to send on a close socket')
        # the timeout is a limit for the whole request (send + receive), not for each recv() call:
        # a slow server sending its response byte by byte can't hold the client more than timeout seconds
        self._deadline = time.monotonic() + self.timeout
        # send
        try:
            # restore the full timeout (it's reduced by _recv() to the time remaining in the previous request)
            self._set_sock_timeout(self.timeout)
            self._sock.sendall(frame)
        except TimeoutError:
            self.close()
            raise NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except OSError:
            self.close()
            raise NetworkError(MB_SEND_ERR, 'send error')

    def _send_pdu(self, pdu: bytes) -> None:
        """Convert modbus PDU to frame and send it.

        :param pdu: modbus frame PDU
        :type pdu: bytes
        """
        # for auto_open mode, check TCP and open on need
        if self.auto_open and not self.is_open:
            self._open()
        # add MBAP header to PDU
        tx_frame = self._add_mbap(pdu)
        # send frame with error check
        self._send(tx_frame)
        # debug
        self._on_tx_rx(frame=tx_frame, is_tx=True)

    def _recv(self, size: int) -> bytes:
        """Receive data over current socket (at most size bytes).

        :param size: max number of bytes to receive
        :type size: int
        :returns: receive data (never empty)
        :rtype: bytes
        :raises NetworkError: on timeout, closed or broken connection
        """
        # check socket status
        if self._sock is None or not self.is_open:
            raise NetworkError(MB_SOCK_CLOSE_ERR, 'try to recv on a close socket')
        # time remaining before the end of the request
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self.close()
            raise NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        try:
            self._set_sock_timeout(remaining)
            r_buffer = self._sock.recv(size)
        except TimeoutError:
            self.close()
            raise NetworkError(MB_TIMEOUT_ERR, 'timeout error')
        except OSError:
            r_buffer = b''
        # handle recv error
        if not r_buffer:
            self.close()
            raise NetworkError(MB_RECV_ERR, 'recv error')
        return r_buffer

    def _recv_all(self, size: int) -> bytes:
        """Receive data over current socket, loop until all bytes is received (avoid TCP frag).

        :param size: number of bytes to receive
        :type size: int
        :returns: receive data
        :rtype: bytes
        """
        r_buffer = b''
        while len(r_buffer) < size:
            r_buffer += self._recv(size - len(r_buffer))
        return r_buffer

    def _recv_pdu(self, func_code: int, min_len: int = 2) -> bytes:
        """Receive the modbus PDU (Protocol Data Unit).

        :param func_code: function code of the request (the reply must have the same)
        :type func_code: int
        :param min_len: minimal length of the PDU
        :type min_len: int
        :returns: modbus frame PDU
        :rtype: bytes
        """
        # receive 7 bytes header (MBAP)
        rx_mbap = self._recv_all(MBAP_SIZE)
        # decode MBAP
        (f_transaction_id, f_protocol_id, f_length, f_unit_id) = struct.unpack('>HHHB', rx_mbap)
        # check MBAP fields
        f_transaction_err = f_transaction_id != self._transaction_id
        f_protocol_err = f_protocol_id != 0
        f_length_err = not MBAP_MIN_LENGTH <= f_length <= MBAP_MAX_LENGTH
        f_unit_id_err = f_unit_id != self.unit_id
        # checking error status of fields: the stream can't be trusted anymore, close it
        if f_transaction_err or f_protocol_err or f_length_err or f_unit_id_err:
            self.close()
            self._on_tx_rx(frame=rx_mbap, is_tx=False)
            raise NetworkError(MB_RECV_ERR, 'MBAP checking error')
        # recv PDU
        rx_pdu = self._recv_all(f_length - 1)
        # for auto_close mode, close socket after each request
        if self.auto_close:
            self.close()
        # dump frame
        self._on_tx_rx(frame=rx_mbap + rx_pdu, is_tx=False)
        # body decode
        # check PDU length for global minimal frame (an except frame: func code + exp code)
        if len(rx_pdu) < 2:
            raise NetworkError(MB_RECV_ERR, 'PDU length is too short')
        # extract function code: it must be the one of the request (with or without the exception flag)
        rx_fc = rx_pdu[0]
        if rx_fc & 0x7f != func_code:
            raise NetworkError(MB_RECV_ERR, 'function code mismatch (0x%02X for a 0x%02X request)'
                               % (rx_fc, func_code))
        # check except status
        if rx_fc >= 0x80:
            exp_code = rx_pdu[1]
            raise ModbusExcept(exp_code)
        # check PDU length for specific request set in min_len (keep this after except checking)
        if len(rx_pdu) < min_len:
            raise NetworkError(MB_RECV_ERR, 'PDU length is too short for current request')
        # if no error, return PDU
        return rx_pdu

    def _add_mbap(self, pdu: bytes) -> bytes:
        """Return full modbus frame with MBAP (modbus application protocol header) append to PDU.

        :param pdu: modbus PDU (protocol data unit)
        :type pdu: bytes
        :returns: full modbus frame
        :rtype: bytes
        """
        # build MBAP
        # next transaction ID (16 bits counter): it always differs from the previous request one, so a late reply
        # of the previous request (left on the socket) is always detected as such and never taken for the current one
        self._transaction_id = (self._transaction_id + 1) & 0xffff
        protocol_id = 0
        length = len(pdu) + 1
        mbap = struct.pack('>HHHB', self._transaction_id, protocol_id, length, self.unit_id)
        # full modbus/TCP frame = [MBAP]PDU
        return mbap + pdu

    def _debug_msg(self, msg: str) -> None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f'({self.host}:{self.port}:{self.unit_id}) {msg}')

    def _on_tx_rx(self, frame: bytes, is_tx: bool) -> None:
        # format a log message
        if logger.isEnabledFor(logging.DEBUG):
            type_s = 'Tx' if is_tx else 'Rx'
            mbap_s = hexlify(frame[0:7], sep=' ').upper().decode()
            pdu_s = hexlify(frame[7:], sep=' ').upper().decode()
            self._debug_msg(f'{type_s} [{mbap_s}] {pdu_s}')
        # notify user
        self.on_tx_rx(frame=frame, is_tx=is_tx)

    def on_tx_rx(self, frame: bytes, is_tx: bool) -> None:
        """Call for each Tx/Rx (for user purposes)."""
