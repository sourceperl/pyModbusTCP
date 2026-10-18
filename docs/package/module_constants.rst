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
   * - ``WRITE_READ_MULTIPLE_REGISTERS``
     - 0x17
     - Read/Write Multiple Registers
   * - ``ENCAPSULATED_INTERFACE_TRANSPORT``
     - 0x2B
     - Encapsulated Interface Transport (with ``MEI_TYPE_READ_DEVICE_ID``, 0x0E: Read Device Identification)

``SUPPORTED_FUNCTION_CODES`` is the tuple of these codes, ``WRITE_FUNCTION_CODES`` the tuple of the write ones.

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
     - name resolve error (not set by ModbusClient: see MB_CONNECT_ERR)
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
     - frame format error (not set by ModbusClient)
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
