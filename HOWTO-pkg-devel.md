## How to set package developer mode (also called editable mode in pip)

*After setting this, we can directly test the effect of editing the package files
without need to fully reinstall it.*

The easy way: `./init_dev_venv.sh` creates a venv (`venv/`) with the test and build tools, the library (with pyserial)
and the `mbtcp-*` commands of pyModbusTCP-cli, all in editable mode (`-h` for its options).

By hand, turn on develop mode (add current package files to python path) in a virtual env:

```bash
python -m venv venv && source venv/bin/activate
# the library only
pip install --editable .
# or the library, pyserial and the mbtcp-* commands (both in one call: the requirement of pyModbusTCP-cli on
# pyModbusTCP is satisfied by this checkout, not by PyPI)
pip install --editable ".[serial]" --editable pyModbusTCP-cli
```
Turn off:

```bash
pip uninstall pyModbusTCP pyModbusTCP-cli
```
View the current python path:

```bash
python -c 'import sys; print(sys.path)'
```
