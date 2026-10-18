Server: command mailbox
=======================

A client sends commands through holding registers: the command register is a mapped range whose write function runs the command in the transaction of the request, so function 0x17 writes a command and reads its result in one atomic request; an unknown command cancels the whole request (run it with ``--demo``).

.. literalinclude:: ../../examples/server_mailbox.py
   :caption: examples/server_mailbox.py
   :language: python
