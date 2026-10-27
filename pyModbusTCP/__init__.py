# Python package: Client and Server for ModBus/TCP
#        Website: https://github.com/sourceperl/pyModbusTCP
#        License: MIT (https://opensource.org/license/mit)
#    Description: Client/Server ModBus/TCP
#                 Functions of the client and the server: 0x01 to 0x06, 0x0F, 0x10, 0x17 and 0x2B/0x0E (read
#                 device identification); any other with ModbusClient.custom_request() and a function handler of
#                 ModbusServer

import logging

from .constants import VERSION

__title__ = 'pyModbusTCP'
__description__ = 'A simple Modbus/TCP library for Python.'
__url__ = 'https://github.com/sourceperl/pyModbusTCP'
__version__ = VERSION
__license__ = 'MIT'

logger = logging.getLogger(__name__)
