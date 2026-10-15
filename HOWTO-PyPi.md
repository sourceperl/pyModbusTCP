## How to upload on PyPI

Here we use the twine tool to do the job, see [Twine setup](#twine-setup) to add and configure it.


### build archive and wheel

The two packages of the repository (the library and pyModbusTCP-cli) are built with hatchling (build backend set in
their pyproject.toml, installed automatically by `build`) and always released together with the same version: the
version of both is `VERSION` in `pyModbusTCP/constants.py`.

```bash
pip install build
python -m build --outdir dist .
python -m build --outdir dist pyModbusTCP-cli
twine check dist/*
```

### version numbers

Set `VERSION` in `pyModbusTCP/constants.py` with a [PEP 440](https://peps.python.org/pep-0440/) version, and the same
version (with the release date) at the top of `CHANGES`:

- `0.4.0.dev0`: development version, never uploaded;
- `0.4.0a1`, `0.4.0a2`...: alpha releases (the interface may still change), then `0.4.0b1`... and `0.4.0rc1`...;
- `0.4.0`: final release.

pip never installs a pre-release (a, b, rc, dev) unless asked for (`pip install pyModbusTCP==0.4.0a1` or `--pre`).
Set the `Development Status` classifier of both `pyproject.toml` (`3 - Alpha`, `4 - Beta`, `5 - Production/Stable`).

An uploaded version can never be uploaded again, even after its deletion: on a mistake, yank it on PyPI and release
the next number.

### upload archive and wheel to PyPi test server

```bash
twine upload dist/pymodbustcp-x.x.x* dist/pymodbustcp_cli-x.x.x* -r pypitest
```

Check result at https://test.pypi.org/project/pyModbusTCP/ and https://test.pypi.org/project/pyModbusTCP-cli/,
then test the install in a new venv (pyserial is not on TestPyPI: take it from PyPI):

```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ \
    "pyModbusTCP[tools]==x.x.x"
mbtcp-client --version
```

### upload archive and wheel to PyPi server

```bash
twine upload dist/pymodbustcp-x.x.x* dist/pymodbustcp_cli-x.x.x* -r pypi
```

Check result at https://pypi.org/project/pyModbusTCP/.


## Twine setup

### install twine

```bash
pip install twine
```

### create it's conf file

Create ~/.pypirc with credentials for pypi and pypitest.

```bash
cat <<EOT >> ~/.pypirc
[distutils]
index-servers =
  pypi
  pypitest

[pypi]
repository: https://upload.pypi.org/legacy/
username: __token__
password: mytoken

[pypitest]
repository: https://test.pypi.org/legacy/
username: __token__
password: mytoken
EOT
```
Update it with valid credentials.

```bash
nano ~/.pypirc
```