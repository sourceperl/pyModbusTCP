#!/usr/bin/env python3

""" An example of basic logging for ModbusClient debugging purposes. """

import logging
import time

from pyModbusTCP.server import ModbusServer
from pyModbusTCP.client import ModbusClient

# a logger for this script
logger = logging.getLogger(__name__)

# global log conf: sets a default format and level for all loggers in this application (include pyModbusTCP)
logging.basicConfig(format='%(asctime)s - %(name)-20s - %(levelname)-8s - %(message)s', level=logging.INFO)
# set debug level for pyModbusTCP.client to see frame exchanges
logging.getLogger('pyModbusTCP.client').setLevel(logging.DEBUG)

# run a modbus server at localhost:5020
ModbusServer(host='localhost', port=5020, no_block=True).start()

# this message is show
logger.info(f'app startup')

# init modbus client to connect to localhost:5020
c = ModbusClient(port=5020)

# main loop
for i in range(100):
    # this message is not not show (global log level is set to INFO)
    logger.debug(f'run loop #{i}')
    # modbus i/o
    c.read_coils(0)
    time.sleep(2)
