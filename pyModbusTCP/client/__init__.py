""" pyModbusTCP Client

The client is split in modules, but everything is imported from here (``from pyModbusTCP.client import ...``):

- modbus_client: ModbusClient, settings, request status and error handling
- _functions: Modbus functions (read coils, write registers...)
- _transport: TCP layer (connection, send/receive of the frames, frames trace)
- _validators: checks of the settings and of the request arguments
- device_id: DeviceIdentificationResponse, result of the read device identification function
- _errors: internal exceptions
"""

from ._common import logger
from .device_id import DeviceIdentificationResponse
from .modbus_client import ModbusClient

__all__ = ['DeviceIdentificationResponse', 'ModbusClient', 'logger']
