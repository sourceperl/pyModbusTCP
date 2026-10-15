pyModbusTCP.server
==================

.. module:: pyModbusTCP.server

The Modbus/TCP server, its data space and the interface to customize it. See :doc:`../guide/server` for an
introduction.

.. code-block:: python

    from pyModbusTCP.server import DataBank, DataHandler, DeviceIdentification, ModbusServer

ModbusServer
------------

.. autoclass:: ModbusServer
   :members:
   :exclude-members: Error, NetworkError, DataFormatError, PDUDecodeError, ClientInfo, ServerInfo, SessionData,
                     Frame, MBAP, PDU, CustomThreadingTCPServer, ModbusRequestHandler

DataBank
--------

.. autoclass:: DataBank
   :members:

DataHandler
-----------

.. autoclass:: DataHandler
   :members:
   :exclude-members: Return

.. class:: DataHandler.Return(exp_code, data=None)

   The result of a :class:`DataHandler` method.

   :param exp_code: ``EXP_NONE`` if the request succeeded, else the Modbus exception code to send to the client
       (constants ``EXP_*`` of :mod:`pyModbusTCP.constants`)
   :type exp_code: int
   :param data: the values of a read request (list of bool or int), None for a write request or an error
   :type data: list or None

   .. attribute:: ok

      True if ``exp_code`` is ``EXP_NONE`` (no exception).

DeviceIdentification
--------------------

.. autoclass:: DeviceIdentification
   :members:

Server and client information
-----------------------------

The ``srv_info`` argument of the :class:`DataBank` and :class:`DataHandler` methods is a ``ModbusServer.ServerInfo``
object, with the information of the client that sent the request:

- ``srv_info.client.address``: IP address of the client (str);
- ``srv_info.client.port``: TCP port of the client (int);
- ``srv_info.recv_frame``: the received request (a ``ModbusServer.Frame``, with its ``mbap`` and ``pdu``).

Exceptions
----------

The exceptions of the server are also available as attributes of :class:`ModbusServer` (like
``ModbusServer.NetworkError``).

.. automodule:: pyModbusTCP.server.errors
   :members:
   :show-inheritance:
