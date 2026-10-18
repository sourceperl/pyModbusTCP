Server: filter by IP address
============================

Two levels of rules: allowed_hosts decides which clients can connect, an AccessControl wrapper on the data store which ones can write.

.. literalinclude:: ../../examples/server_allow.py
   :caption: examples/server_allow.py
   :language: python
