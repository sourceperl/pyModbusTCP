Server: device identification
=============================

Answer the "Read Device Identification" function (0x2B/0x0E): basic, regular, extended and private objects, read by
the clients by category (never publish anything sensitive: every client can read them).

.. literalinclude:: ../../examples/server_device_id.py
   :caption: examples/server_device_id.py
   :language: python
