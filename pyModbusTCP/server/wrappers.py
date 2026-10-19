""" pyModbusTCP server: store wrappers, access rules stacked on a data store.

A wrapper is a data store that checks each access of a client request, then passes it to the store it wraps::

    store = AccessControl(ReadOnly(MemoryStore(), spaces=[Space.COILS]), write_hosts=['192.168.1.20'])

The rules only apply to the client requests: the application (store.app, transactions without context) always has a
full access. A refused access raises a ModbusException (the client gets the exception response).
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from typing import Any, Literal

from . import _validators
from ._common import client_ip
from .errors import IllegalDataAddress, IllegalFunction
from .store import AppAccess, DataStore, Listener, RequestContext, Space, Transaction

# the kind of access checked by a wrapper
Access = Literal['read', 'write']


class _CheckedTransaction(Transaction):
    """A transaction that asks the wrapper before each access."""

    def __init__(self, wrapper: StoreWrapper, tx: Transaction, ctx: RequestContext) -> None:
        # ctx (public, of Transaction) is "RequestContext | None"; _ctx keeps the type of a client request (never
        # None here) for check(), without a narrowed override of ctx (a mutable attribute)
        self.ctx = ctx
        self._ctx = ctx
        self._wrapper = wrapper
        self._tx = tx

    def read(self, space: Space, address: int, count: int = 1) -> list[Any]:
        self._wrapper.check(self._ctx, 'read', space, address, count)
        return self._tx.read(space, address, count)

    def write(self, space: Space, address: int, values: Iterable[Any]) -> None:
        values = list(values)
        self._wrapper.check(self._ctx, 'write', space, address, len(values))
        self._tx.write(space, address, values)


class StoreWrapper(DataStore):
    """Base class of the wrappers: override check() to refuse some accesses of the clients.

    The other attributes of the wrapped store (like MemoryStore.map()) are available on the wrapper.
    """

    def __init__(self, store: DataStore) -> None:
        if not isinstance(store, DataStore):
            raise TypeError('store must be a DataStore')
        self.store = store

    def __repr__(self) -> str:
        return f'{type(self).__name__}({self.store!r})'

    def __getattr__(self, name: str) -> Any:
        # only called for a missing attribute: the extras of the wrapped store (map(), size()...)
        if name == 'store':
            raise AttributeError(name)
        return getattr(self.store, name)

    def check(self, ctx: RequestContext, access: Access, space: Space, address: int, count: int) -> None:
        """Check an access of a client request: raise a ModbusException to refuse it (default: allowed)."""

    @contextmanager
    def transaction(self, ctx: RequestContext | None = None) -> Generator[Transaction, None, None]:
        with self.store.transaction(ctx) as tx:
            yield tx if ctx is None else _CheckedTransaction(self, tx, ctx)

    def subscribe(self, listener: Listener, app_writes: bool = False, threaded: bool = False) -> Callable[[], None]:
        return self.store.subscribe(listener, app_writes, threaded)

    @property
    def app(self) -> AppAccess:
        return self.store.app


class ReadOnly(StoreWrapper):
    """Refuse the client writes ("illegal function" exception), in every space or in some of them."""

    def __init__(self, store: DataStore, spaces: Iterable[Space] | None = None) -> None:
        """
        :param store: the wrapped store
        :param spaces: the read-only spaces (default: every space)
        """
        super().__init__(store)
        self.spaces = frozenset(Space) if spaces is None else frozenset(spaces)

    def check(self, ctx: RequestContext, access: Access, space: Space, address: int, count: int) -> None:
        if access == 'write' and space in self.spaces:
            raise IllegalFunction(f'{space.value} are read-only')


class AccessControl(StoreWrapper):
    """Allow the reads and the writes only to some client addresses or networks ("illegal function" exception).

    This filters on the source IP address, it's not an authentication.
    """

    def __init__(self, store: DataStore, read_hosts: Iterable[str] | None = None,
                 write_hosts: Iterable[str] | None = None) -> None:
        """
        :param store: the wrapped store
        :param read_hosts: IP addresses or networks allowed to read (like ['192.168.1.0/24']), None for every client,
            an empty list for no client
        :param write_hosts: IP addresses or networks allowed to write, None for every client, an empty list for no
            client
        """
        super().__init__(store)
        self._read_networks = _validators.validate_allowed_hosts(read_hosts, 'read_hosts')
        self._write_networks = _validators.validate_allowed_hosts(write_hosts, 'write_hosts')

    def check(self, ctx: RequestContext, access: Access, space: Space, address: int, count: int) -> None:
        networks = self._read_networks if access == 'read' else self._write_networks
        if networks is None:
            return
        ip = client_ip((ctx.client.address, ctx.client.port))
        if ip is None or not any(ip in network for network in networks):
            raise IllegalFunction(f'{access} not allowed for {ctx.client.address}')


class Guard(StoreWrapper):
    """Check each client access with a function: check(ctx, access, space, address, count).

    The function returns False to refuse the access ("illegal data address" exception), or raises a ModbusException
    for another exception code.
    """

    def __init__(self, store: DataStore,
                 check: Callable[[RequestContext, Access, Space, int, int], bool | None]) -> None:
        super().__init__(store)
        self._check = check

    def check(self, ctx: RequestContext, access: Access, space: Space, address: int, count: int) -> None:
        if self._check(ctx, access, space, address, count) is False:
            raise IllegalDataAddress(f'{access} of {space.value} @{address} refused')
