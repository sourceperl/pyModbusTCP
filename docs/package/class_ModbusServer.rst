pyModbusTCP.server
==================

.. module:: pyModbusTCP.server

The Modbus/TCP server, its data store, the access rules and the function handlers. See :doc:`../guide/server` for an
introduction.

.. code-block:: python

    from pyModbusTCP.server import MemoryStore, ModbusServer, Space

ModbusServer
------------

.. autoclass:: ModbusServer
   :members:
   :exclude-members: Error, NetworkError, DataFormatError, PDUDecodeError, ClientInfo, SessionData,
                     Frame, MBAP, PDU, CustomThreadingTCPServer, ModbusRequestHandler

Data store
----------

.. autoclass:: Space
   :members:

.. autoclass:: DataStore
   :members:

.. autoclass:: MemoryStore
   :members:

.. autoclass:: Transaction
   :members:

.. autoclass:: RequestContext
   :members:

The ``client`` of a request context is a ``ModbusServer.ClientInfo``: ``client.address`` is the IP address of the
client (str), ``client.port`` its TCP port (int).

Change notifications
--------------------

.. autoclass:: ChangeEvent
   :members:

.. autoclass:: Change
   :members:

Application access
------------------

.. autoclass:: AppAccess
   :members:

.. autoclass:: SpaceAccess
   :members:

.. autoclass:: RegistersAccess
   :members:
   :show-inheritance:

Access rules
------------

.. autoclass:: StoreWrapper
   :members: check

.. autoclass:: ReadOnly
   :show-inheritance:

.. autoclass:: AccessControl
   :show-inheritance:

.. autoclass:: Guard
   :show-inheritance:

Function handlers
-----------------

.. autoclass:: Request
   :members:

.. data:: FunctionHandler

   The type of a function handler: ``Callable[[Request], bytes]``, it returns the response PDU (the function code
   first) and raises a :class:`ModbusException` for an exception response.

.. data:: STANDARD_FUNCTIONS

   The handlers of the standard functions, by function code (0x01 to 0x06, 0x0F, 0x10, 0x17 and 0x2B/0x0E). A copy is
   the default of ``ModbusServer.functions``.

DeviceIdentification
--------------------

.. autoclass:: DeviceIdentification
   :members:

Modbus exceptions
-----------------

Raise them from a store, a wrapper, a mapped range or a function handler: the client gets the exception response,
its session goes on.

.. autoclass:: ModbusException
.. autoclass:: IllegalFunction
   :show-inheritance:
.. autoclass:: IllegalDataAddress
   :show-inheritance:
.. autoclass:: IllegalDataValue
   :show-inheritance:
.. autoclass:: SlaveDeviceFailure
   :show-inheritance:
.. autoclass:: SlaveDeviceBusy
   :show-inheritance:
.. autoclass:: GatewayPathUnavailable
   :show-inheritance:
.. autoclass:: GatewayTargetFailed
   :show-inheritance:

Errors of the server
--------------------

The errors of the server are also available as attributes of :class:`ModbusServer` (like
``ModbusServer.NetworkError``).

.. autoclass:: pyModbusTCP.server.errors.Error
   :show-inheritance:
.. autoclass:: pyModbusTCP.server.errors.NetworkError
   :show-inheritance:
.. autoclass:: pyModbusTCP.server.errors.DataFormatError
   :show-inheritance:
.. autoclass:: pyModbusTCP.server.errors.PDUDecodeError
   :show-inheritance:
