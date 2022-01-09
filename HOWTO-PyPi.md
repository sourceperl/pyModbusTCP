## How to upload on PyPI

Here we use the twine tool to do the job, see [Twine setup](#twine-setup) to add and configure it.


### build archive

```bash
sudo python setup.py sdist
```

### upload archive to PyPi test server

```bash
twine upload dist/pyModbusTCP-x.x.x.tar.gz -r pypitest
```

Check result at https://test.pypi.org/project/pyModbusTCP/.

### upload archive to PyPi server

```bash
twine upload dist/pyModbusTCP-x.x.x.tar.gz -r pypi
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
