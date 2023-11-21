## How to upload on PyPI

Here we use the twine tool to do the job, see [Twine setup](#twine-setup) to add and configure it.


### build archive and wheel

```bash
python setup.py sdist bdist_wheel
```

### upload archive and wheel to PyPi test server

```bash
twine upload dist/pyModbusTCP-x.x.x* -r pypitest
```

Check result at https://test.pypi.org/project/pyModbusTCP/.

### upload archive and wheel to PyPi server

```bash
twine upload dist/pyModbusTCP-x.x.x* -r pypi
```

Check result at https://pypi.python.org/project/pyModbusTCP/.


## Twine setup

### install twine

```bash
sudo pip install twine
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
username: myname
password: mypwd

[pypitest]
repository: https://test.pypi.org/legacy/
username: myname
password: mypwd
EOT
```
Update it with valid credentials.

```bash
nano ~/.pypirc
```
