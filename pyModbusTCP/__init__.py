# -*- coding: utf-8 -*-

# Python package: Client and Server for ModBus/TCP
#        Version: 0.1.9
#        Website: https://github.com/sourceperl/pyModbusTCP
#           Date: 2021-02-26
#        License: MIT (http://http://opensource.org/licenses/mit-license.php)
#    Description: Client/Server ModBus/TCP
#                 Support functions 3 and 16 (class 0)
#                 1,2,4,5,6 (Class 1)
#                 15
#        Charset: utf-8

__all__ = ['constants', 'client', 'server', 'utils']
from .constants import VERSION as __version__
