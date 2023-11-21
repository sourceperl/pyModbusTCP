## How to set package developer mode (also call editable mode on pip)

*After set this, we can directly test effect of editing a package files
without need to fully reinstall it.*

Turn on develop mode (add current package files to python path) in a virtual env:

```bash
python -m venv venv && source venv/bin/activate
pip install --editable .
```
Turn off:

```bash
pip uninstall pyModbusTCP
```
View the current python path:

```bash
python -c 'import sys; print(sys.path)'
```
