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
     - A ModbusClient subclass to read and write 32 bits IEEE floats with the utils functions.
   * - :doc:`client_thread`
     - Poll registers in a thread, share the values with the main thread through a lock.
   * - :doc:`client_debug`
     - Show the frames of the client with the logging module (a local server is started for the demo).

Server
------

.. list-table::
   :widths: 35 65

   * - :doc:`server`
     - A Modbus/TCP server with its default data bank, host and port from the command line.
   * - :doc:`server_allow`
     - A custom DataHandler that allows reads and writes only for some client addresses.
   * - :doc:`server_change_log`
     - A custom DataBank that logs every coil and holding register changed by a client.
   * - :doc:`server_dynamic_port`
     - Let the operating system choose a free port (port=0) and read it back.
   * - :doc:`server_schedule`
     - A server stopped every day from 18:00 to 06:00, with a life word updated every 10 seconds (needs the schedule package).
   * - :doc:`server_virtual_data`
     - A virtual DataBank: the holding registers 0 to 5 give the current date and time, computed at each request.

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
   server_dynamic_port
   server_schedule
   server_virtual_data
