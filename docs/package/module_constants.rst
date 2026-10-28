pyModbusTCP.constants
=====================

.. module:: pyModbusTCP.constants

The constants of the package: Modbus function codes, Modbus exception codes and client error codes.

.. code-block:: python

    from pyModbusTCP.constants import EXP_ILLEGAL_FUNCTION, MB_EXCEPT_ERR

Function codes
--------------

.. list-table::
   :header-rows: 1
   :widths: 50 15 35

   * - Constant
     - Value
     - Function
   * - ``READ_COILS``
     - 0x01
     - Read Coils
   * - ``READ_DISCRETE_INPUTS``
     - 0x02
     - Read Discrete Inputs
   * - ``READ_HOLDING_REGISTERS``
     - 0x03
     - Read Holding Registers
   * - ``READ_INPUT_REGISTERS``
     - 0x04
     - Read Input Registers
   * - ``WRITE_SINGLE_COIL``
     - 0x05
     - Write Single Coil
   * - ``WRITE_SINGLE_REGISTER``
     - 0x06
     - Write Single Register
   * - ``WRITE_MULTIPLE_COILS``
     - 0x0F
     - Write Multiple Coils
   * - ``WRITE_MULTIPLE_REGISTERS``
     - 0x10
     - Write Multiple Registers
   * - ``WRITE_FILE_RECORD``
     - 0x15
     - Write File Record (not implemented: see below)
   * - ``MASK_WRITE_REGISTER``
     - 0x16
     - Mask Write Register (not implemented: see below)
   * - ``WRITE_READ_MULTIPLE_REGISTERS``
     - 0x17
     - Read/Write Multiple Registers
   * - ``ENCAPSULATED_INTERFACE_TRANSPORT``
     - 0x2B
     - Encapsulated Interface Transport (with ``MEI_TYPE_READ_DEVICE_ID``, 0x0E: Read Device Identification)

The tuples of codes:

- ``SUPPORTED_FUNCTION_CODES``: the functions implemented by the client and the server of the package, all the codes
  of the table but 0x15 and 0x16 (``ModbusClient.custom_request()`` sends any other code, a handler of the server
  answers it).
- ``WRITE_FUNCTION_CODES``: the standard functions that write data, 0x05, 0x06, 0x0F, 0x10, 0x15, 0x16 and 0x17.
  ``ModbusServer(read_only=True)`` refuses them whatever their handler: 0x15 and 0x16 are not implemented, but a
  handler of the application (or a gateway) could answer them.
- ``READ_FUNCTION_CODES``: the functions that only read data, 0x01 to 0x04 and 0x2B (with the MEI type 0x0E): the
  only ones that a read-only server gives to its fallback handler.

Modbus exception codes
----------------------

The codes a server sends back when it can't process a request: :attr:`ModbusClient.last_except
<pyModbusTCP.client.ModbusClient.last_except>` on the client side, the ``code`` of a
:class:`~pyModbusTCP.server.ModbusException` on the server side. ``EXP_TXT`` and
``EXP_DETAILS`` map each code to a short and a long description.

.. list-table::
   :header-rows: 1
   :widths: 50 15 35

   * - Constant
     - Value
     - Meaning
   * - ``EXP_NONE``
     - 0x00
     - no exception
   * - ``EXP_ILLEGAL_FUNCTION``
     - 0x01
     - illegal function
   * - ``EXP_DATA_ADDRESS``
     - 0x02
     - illegal data address
   * - ``EXP_DATA_VALUE``
     - 0x03
     - illegal data value
   * - ``EXP_SLAVE_DEVICE_FAILURE``
     - 0x04
     - slave device failure
   * - ``EXP_ACKNOWLEDGE``
     - 0x05
     - acknowledge
   * - ``EXP_SLAVE_DEVICE_BUSY``
     - 0x06
     - slave device busy
   * - ``EXP_NEGATIVE_ACKNOWLEDGE``
     - 0x07
     - negative acknowledge
   * - ``EXP_MEMORY_PARITY_ERROR``
     - 0x08
     - memory parity error
   * - ``EXP_GATEWAY_PATH_UNAVAILABLE``
     - 0x0A
     - gateway path unavailable
   * - ``EXP_GATEWAY_TARGET_DEVICE_FAILED_TO_RESPOND``
     - 0x0B
     - gateway target device failed to respond

Client error codes
------------------

The values of :attr:`ModbusClient.last_error <pyModbusTCP.client.ModbusClient.last_error>` (see
:ref:`client-errors` for their meaning). ``MB_ERR_TXT`` maps each code to a description.

.. list-table::
   :header-rows: 1
   :widths: 50 15 35

   * - Constant
     - Value
     - Description
   * - ``MB_NO_ERR``
     - 0
     - no error
   * - ``MB_RESOLVE_ERR``
     - 1
     - name resolve error
   * - ``MB_CONNECT_ERR``
     - 2
     - connect error
   * - ``MB_SEND_ERR``
     - 3
     - socket send error
   * - ``MB_RECV_ERR``
     - 4
     - socket recv error
   * - ``MB_TIMEOUT_ERR``
     - 5
     - recv timeout occur
   * - ``MB_FRAME_ERR``
     - 6
     - frame format error
   * - ``MB_EXCEPT_ERR``
     - 7
     - modbus exception
   * - ``MB_CRC_ERR``
     - 8
     - bad CRC on receive frame (not set by ModbusClient)
   * - ``MB_SOCK_CLOSE_ERR``
     - 9
     - socket is closed

Other constants
---------------

- ``VERSION``: the version of the package (also ``pyModbusTCP.__version__``).
- ``MODBUS_PORT``: the standard Modbus/TCP port, 502.
- ``MAX_PDU_SIZE`` (253), ``MIN_PDU_SIZE`` (1), ``MBAP_SIZE`` (7): frame sizes, in bytes.
- ``MBAP_MIN_LENGTH`` (2), ``MBAP_MAX_LENGTH`` (254): the range of the length field of the MBAP header (the unit id
  and the PDU, ``MIN_PDU_SIZE + 1`` to ``MAX_PDU_SIZE + 1``).
