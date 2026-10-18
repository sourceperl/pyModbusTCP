Examples
========

The scripts of the `examples/ <https://github.com/sourceperl/pyModbusTCP/tree/master/examples>`_ directory of the
repository.

.. note::

    The examples are written for the version of pyModbusTCP of this documentation: they may not work with an older
    release. Most servers listen on port 502 by default: on Unix systems, run them with ``--port 5020`` (or see
    :doc:`../guide/tools` to listen on port 502 without root).

Client
------

.. list-table::
   :widths: 35 65

   * - :doc:`client_minimal`
     - Read 3 coils of a server on localhost, in one line.
   * - :doc:`client_read_coils`
     - Read 10 coils every 2 seconds and print them.
   * - :doc:`client_read_h_registers`
     - Read 10 holding registers every 2 seconds and print them.
   * - :doc:`client_write_coils`
     - Write the coils 0 to 3 one by one, read them back, then do it again with the opposite value.
   * - :doc:`client_float`
     - Write and read float32 values with a Codec.
   * - :doc:`client_thread`
     - Poll registers in a thread, share the values with the main thread through a lock.
   * - :doc:`client_debug`
     - Show the frames of the client with the logging module (a local server is started for the demo).

Server
------

.. list-table::
   :widths: 35 65

   * - :doc:`server`
     - A Modbus/TCP server with its default data store, host and port from the command line.
   * - :doc:`server_allow`
     - allowed_hosts for the clients that can connect, an AccessControl wrapper for the ones that can write.
   * - :doc:`server_change_log`
     - A change listener that logs every coil and holding register changed by a client.
   * - :doc:`server_custom_function`
     - Add a function 0x41, replace the standard function 0x06, log the unsupported functions with a fallback.
   * - :doc:`server_dynamic_port`
     - Let the operating system choose a free port (port=0) and read it back.
   * - :doc:`server_schedule`
     - A server stopped every day from 18:00 to 06:00, with a life word updated every 10 seconds (needs the schedule package).
   * - :doc:`server_mailbox`
     - A command mailbox: a mapped command register runs the command in the request, 0x17 gets its result at once.
   * - :doc:`server_multi_unit`
     - One data store per unit id: a power meter (32 bits floats) and a valve controller on one server.
   * - :doc:`server_virtual_data`
     - A mapped range: the holding registers 0 to 5 give the current date and time, computed at each request.

.. toctree::
   :hidden:

   client_minimal
   client_read_coils
   client_read_h_registers
   client_write_coils
   client_float
   client_thread
   client_debug
   server
   server_allow
   server_change_log
   server_custom_function
   server_dynamic_port
   server_schedule
   server_mailbox
   server_multi_unit
   server_virtual_data
