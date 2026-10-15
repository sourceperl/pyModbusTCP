""" pyModbusTCP server: session containers (client info, server info and session data passed to engines). """

from __future__ import annotations

from .frames import Frame


class ClientInfo:
    """ Container class for client information """

    def __init__(self, address: str = '', port: int = 0) -> None:
        self.address = address
        self.port = port

    def __repr__(self) -> str:
        return 'ClientInfo(address=%r, port=%r)' % (self.address, self.port)


class ServerInfo:
    """ Container class for server information """

    def __init__(self) -> None:
        self.client = ClientInfo()
        self.recv_frame = Frame()


class SessionData:
    """ Container class for server session data. """

    def __init__(self) -> None:
        self.client = ClientInfo()
        self.request = Frame()
        self.response = Frame()

    @property
    def srv_info(self) -> ServerInfo:
        info = ServerInfo()
        info.client = self.client
        info.recv_frame = self.request
        return info

    def new_request(self) -> None:
        self.request = Frame()
        self.response = Frame()

    def set_response_mbap(self) -> None:
        self.response.mbap.transaction_id = self.request.mbap.transaction_id
        self.response.mbap.protocol_id = self.request.mbap.protocol_id
        self.response.mbap.unit_id = self.request.mbap.unit_id
