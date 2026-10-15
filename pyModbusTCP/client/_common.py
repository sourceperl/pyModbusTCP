""" pyModbusTCP client: shared internals (logger, Modbus limits). """

import logging

# all the client modules log under the historical name "pyModbusTCP.client" (used by applications to tune it)
logger = logging.getLogger('pyModbusTCP.client')

# modbus limits (number of items by request)
MAX_READ_BITS = 2000
MAX_WRITE_BITS = 1968
MAX_READ_REGS = 125
MAX_WRITE_REGS = 123
MAX_WRITE_READ_REGS = 121
