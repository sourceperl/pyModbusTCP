""" pyModbusTCP server: session containers (client info and the data of the current request of a session). """

from __future__ import annotations

from .frames import Frame


class ClientInfo:
    """ Container class for client information """

    def __init__(self, address: str = '', port: int = 0) -> None:
        self.address = address
        self.port = port

    def __repr__(self) -> str:
        return f'ClientInfo(address={self.address!r}, port={self.port!r})'


class SessionData:
    """ Container class for server session data. """

    def __init__(self) -> None:
        self.client = ClientInfo()
        self.request = Frame()
        self.response = Frame()

    def new_request(self) -> None:
        self.request = Frame()
        self.response = Frame()

    def set_response_mbap(self) -> None:
        self.response.mbap.transaction_id = self.request.mbap.transaction_id
        self.response.mbap.protocol_id = self.request.mbap.protocol_id
        self.response.mbap.unit_id = self.request.mbap.unit_id
