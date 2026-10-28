## How to upload on PyPI

Here we use the twine tool to do the job (installed by `./init_dev_venv.sh`). There is no `~/.pypirc` and no stored
credential: twine asks for an API token at each upload.


### build archive and wheel

The two packages of the repository (the library and pyModbusTCP-cli) are built with hatchling and always released
together with the same version: the version of both is `VERSION` in `pyModbusTCP/constants.py`.

Build them with `tools/build_dist.sh`, in the dev venv of `./init_dev_venv.sh`:

```bash
source venv/bin/activate
tools/build_dist.sh -t
```

It builds without build isolation (`python -m build --no-isolation`): the build backend is the hatchling pinned in
`requirements/ci.txt` (installed in the dev venv), the one of the CI. A plain `python -m build` would build in an
isolated environment with the latest hatchling from PyPI, a version never tested here (and a compromised release would
run at once). Don't build the packages by hand.

It builds the 4 files in `dist/`, runs `twine check --strict`, checks that pyModbusTCP-cli requires exactly this
release and that the `cli` extra of the library requires exactly this release of pyModbusTCP-cli
(`tools/check_dist_requires.py`, the two requirements are set at build time by `hatch_metadata.py` and
`pyModbusTCP-cli/hatch_metadata_cli.py`), that the first entry of `CHANGES` is `VERSION YYYY-MM-DD` (a placeholder date
can't be fixed after the upload: the changelog is in the sdist) and that the docs pin this release
(`pip install "pyModbusTCP[cli]==VERSION"`: only a warning for a `.devN` version, whose docs pin the release it
prepares), warns about a version already on PyPI or TestPyPI, then (`-t`) tests the install of the wheels in
throwaway venvs.

Before a release, also run the test suite, `mypy --strict pyModbusTCP` and the docs build (`tools/build_docs.sh`).

### version numbers

Set `VERSION` in `pyModbusTCP/constants.py` with a [PEP 440](https://peps.python.org/pep-0440/) version, and the same
version (with the release date) at the top of `CHANGES`:

- `0.4.0.dev0`: development version, never uploaded; between two pre-releases, the next one with `.dev0`
  (`0.4.0a4.dev0` after `0.4.0a3`): the tree never claims a published version it differs from;
- `0.4.0a1`, `0.4.0a2`...: alpha releases (the interface may still change), then `0.4.0b1`... and `0.4.0rc1`...;
- `0.4.0`: final release.

pip never installs a pre-release (a, b, rc, dev) unless asked for (`pip install pyModbusTCP==0.4.0a1` or `--pre`).
Set the `Development Status` classifier of both `pyproject.toml` (`3 - Alpha`, `4 - Beta`, `5 - Production/Stable`).

An uploaded version can never be uploaded again, even after its deletion: on a mistake, yank it on PyPI and release
the next number.

### upload archive and wheel to PyPI test server

pyModbusTCP and pyModbusTCP-cli are two projects on PyPI, and a token scoped to a project can upload to this project
only: upload the library first (pyModbusTCP-cli requires it), then the commands, each with its own token. twine asks
for the token (hidden input, "Enter your API token"), the username is `__token__` by default.

```bash
VERSION=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" pyModbusTCP/constants.py)
# no stored credential is ever used: no keyring (a token saved there long ago would be taken silently)
export PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring
twine upload -r testpypi dist/pymodbustcp-"$VERSION"*       # token of pyModbusTCP on TestPyPI
twine upload -r testpypi dist/pymodbustcp_cli-"$VERSION"*   # token of pyModbusTCP-cli on TestPyPI
```

Check result at https://test.pypi.org/project/pyModbusTCP/ and https://test.pypi.org/project/pyModbusTCP-cli/,
then test the install in a new venv: the two packages from TestPyPI only (`--no-deps`: never a dependency from
TestPyPI, where anyone can upload any name), pyserial from PyPI:

```bash
python -m venv /tmp/check-release
/tmp/check-release/bin/pip install --index-url https://test.pypi.org/simple/ --no-deps \
    "pyModbusTCP==$VERSION" "pyModbusTCP-cli==$VERSION"
/tmp/check-release/bin/pip install "pyserial>=3.5,<4"
/tmp/check-release/bin/pip check
/tmp/check-release/bin/mbtcp-client --version
rm -rf /tmp/check-release
```

TestPyPI may take a minute to list a new release: retry the install if it is not found at once.

### upload archive and wheel to PyPI server

The same files as on TestPyPI (never build again between the two uploads), with the tokens of PyPI:

```bash
twine upload -r pypi dist/pymodbustcp-"$VERSION"*       # token of pyModbusTCP on PyPI
twine upload -r pypi dist/pymodbustcp_cli-"$VERSION"*   # token of pyModbusTCP-cli on PyPI
```

Check result at https://pypi.org/project/pyModbusTCP/ and https://pypi.org/project/pyModbusTCP-cli/.
