Server: two devices on one server
=================================

One data store per unit id: a read-only power meter (32 bits floats written with the typed access) and a valve controller.

.. literalinclude:: ../../examples/server_multi_unit.py
   :caption: examples/server_multi_unit.py
   :language: python
