## How to upload on PyPI

Here we use the twine tool to do the job, see [Twine setup](#twine-setup) to add and configure it.


### build archive and wheel

The two packages of the repository (the library and pyModbusTCP-tools) are built with hatchling (build backend set in
their pyproject.toml, installed automatically by `build`) and always released together with the same version: the
version of both is `VERSION` in `pyModbusTCP/constants.py`.

```bash
pip install build
python -m build --outdir dist .
python -m build --outdir dist pyModbusTCP-tools
twine check dist/*
```

`python -m build` builds the sdist, then the wheel from this sdist: a check that the sdist is complete. The content of
the sdist of the library is set by `[tool.hatch.build.targets.sdist]` in `pyproject.toml` (no MANIFEST.in).

### upload archive and wheel to PyPi test server

```bash
twine upload dist/pymodbustcp-x.x.x* dist/pymodbustcp_tools-x.x.x* -r pypitest
```

Check result at https://test.pypi.org/project/pyModbusTCP/.

### upload archive and wheel to PyPi server

```bash
twine upload dist/pymodbustcp-x.x.x* dist/pymodbustcp_tools-x.x.x* -r pypi
```

Check result at https://pypi.python.org/project/pyModbusTCP/.


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
