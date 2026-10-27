"""Command line tools of pyModbusTCP.

The tools are part of the library, but a plain "pip install pyModbusTCP" puts no command in the PATH. Run them with:

    python -m pyModbusTCP.tools server ...      # Modbus/TCP server
    python -m pyModbusTCP.tools client ...      # Modbus/TCP client (read and write requests)
    python -m pyModbusTCP.tools tcp2rtu ...     # Modbus/TCP -> Modbus/RTU gateway (needs pyserial)
    python -m pyModbusTCP.tools rtu2tcp ...     # Modbus/RTU -> Modbus/TCP gateway (needs pyserial)
    python -m pyModbusTCP.tools rtu-scan ...    # find the serial settings and address of RTU slaves (pyserial)

The pyModbusTCP-cli package adds them as commands (mbtcp-server, mbtcp-client, mbtcp-tcp2rtu, mbtcp-rtu2tcp and
mbtcp-rtu-scan) and installs pyserial: "pip install pyModbusTCP[cli]" or "pipx install pyModbusTCP-cli".
The serial tools only need the optional pyserial dependency: "pip install pyModbusTCP[serial]".

The modules of this package are command line programs: their content is not a stable API.
"""
