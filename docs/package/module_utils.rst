Module pyModbusTCP.utils
========================

*This module provide a set of functions for modbus data mangling.*

Bit functions
-------------

.. automodule:: pyModbusTCP.utils
   :members: byte_length, get_bits_from_int, reset_bit, set_bit, test_bit, toggle_bit

Word functions
--------------

.. automodule:: pyModbusTCP.utils
   :members: long_list_to_word, word_list_to_long

Two's complement functions
--------------------------

.. automodule:: pyModbusTCP.utils
   :members: get_2comp, get_list_2comp

IEEE floating-point functions
-----------------------------

.. automodule:: pyModbusTCP.utils
   :members: decode_ieee, encode_ieee

Misc functions
--------------

.. automodule:: pyModbusTCP.utils
   :members: crc16, valid_host
