## Important notice

**The examples in this directory are designed to work with the version of pyModbusTCP currently in this repository.**

They may or may not work with the PyPI version which is always the latest stable version and not the development one.

## Listen on the standard Modbus port 502 without root

Modbus/TCP uses port 502. On Unix systems the ports below 1024 are "privileged": by default only root can listen on
them. **Don't run a server as root (or with sudo) to get port 502**: any flaw in the script, in a callback of your own
or in a library would then give full control of the host to the attacker, and Modbus clients are never authenticated.

Instead, give the `CAP_NET_BIND_SERVICE` capability (the right to listen on a port below 1024, and nothing else) to the
Python interpreter of a virtual environment dedicated to the Modbus server. The server then runs as a regular user.

### 1. Create the virtual environment with real copies of the interpreter

The `--copies` option is required: without it, `venv/bin/python3` is only a symbolic link to the system interpreter,
and `setcap` refuses it (`Invalid file ... for capability operation`). This refusal protects you: following the link
would give the capability to `/usr/bin/python3`, so to every Python script of the host.

```bash
python3 -m venv --copies venv
venv/bin/pip install pyModbusTCP
```

To convert an existing virtual environment (installed packages are kept):

```bash
find venv/bin -maxdepth 1 -type l -name 'python*' -delete   # remove the python, python3, python3.x links
python3 -m venv --copies --upgrade venv                     # replace them by real copies
```

### 2. Give the capability to the interpreter

```bash
sudo setcap 'cap_net_bind_service=+ep' venv/bin/python3
getcap venv/bin/python3          # check, must print: venv/bin/python3 cap_net_bind_service=ep
```

### 3. Run the server as a regular user

```bash
venv/bin/python3 examples/server.py --host 192.168.1.10 --port 502
```

Use this exact file (`venv/bin/python3`): `venv/bin/python` is another copy, without the capability.

### Precautions

- Every script run with this interpreter can listen on a low port: keep this virtual environment for the Modbus server
  only, and don't let other users modify it.
- Never give the capability to a development venv (like the one of `./init_dev_venv.sh`): pip, pytest, the build tools
  and every package installed there would run with it. That venv uses links to the system interpreter, so `setcap`
  refuses it; to test a server there, use a port above 1023 (`--port 5020`).
- Never give the capability to the system interpreter (`/usr/bin/python3`), and never work around the error above with
  `setcap ... $(readlink -f venv/bin/python3)`: that is the system interpreter.
- The capability is lost when the interpreter is replaced (Python update, `--upgrade`, venv recreated): run `setcap`
  again.
- To remove it: `sudo setcap -r venv/bin/python3`.

### In every case

Listening on port 502 is only the first step. Limit who can reach the server (see [SECURITY.md](../SECURITY.md)):

- listen on the address of the network interface that needs it (`--host 192.168.1.10`), not on `0.0.0.0`;
- filter the clients on the firewall, and with the `allowed_hosts` option of `ModbusServer` as a second line of
  defense;
- use `read_only=True` if the clients only need to read data;
- never expose port 502 on the Internet.
