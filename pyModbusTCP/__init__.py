# -*- coding: utf-8 -*-

# Python package: Client and Server for ModBus/TCP
#        Version: 0.1.10
#        Website: https://github.com/sourceperl/pyModbusTCP
#           Date: 2021-03-02
#        License: MIT (http://http://opensource.org/licenses/mit-license.php)
#    Description: Client/Server ModBus/TCP
#                 Support functions 3 and 16 (class 0)
#                 1,2,4,5,6 (Class 1)
#                 15
#        Charset: utf-8

from .constants import VERSION


__all__ = ['constants', 'client', 'server', 'utils']
__title__ = 'pyModbusTCP'
__description__ = 'A simple Modbus/TCP library for Python.'
__url__ = 'https://github.com/sourceperl/pyModbusTCP'
__version__ = VERSION
__license__ = 'MIT'
