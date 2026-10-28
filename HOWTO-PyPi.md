## How to upload on PyPI

The packages are uploaded by the workflow `.github/workflows/release.yml`, with the
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) of PyPI: no API token exists, neither on a computer nor
in the secrets of the repository. Each upload job of the workflow gets a short-lived OpenID Connect token from GitHub,
that PyPI exchanges for an upload token valid a few minutes, for this repository, this workflow file and its
environment only. The uploads also publish the attestations of the files (PEP 740): anyone can check that a file of
PyPI was built by this workflow, from this repository, at the commit of the tag (see "check a release" below).


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

### set up the Trusted Publishing (once)

1. On GitHub (Settings, Environments), create the 2 environments of the workflow:
   - `testpypi`: deployment branches and tags, "Selected branches and tags", the tag rule `v*`;
   - `pypi`: the same tag rule, and the "Required reviewers" protection with the maintainers: the upload to PyPI waits
     for the approval of one of them, once the upload to TestPyPI and its install check have passed.
2. On GitHub (Settings, Rules, Rulesets), protect the tags `v*`: a new ruleset of tags, target `v*`, with
   "Restrict creations", "Restrict updates" and "Restrict deletions" (bypass: the maintainers only). A tag pushed by
   anyone else would start a release.
3. On PyPI and on TestPyPI, add a trusted publisher to each project (pyModbusTCP and pyModbusTCP-cli: 4 publishers),
   in "Manage project", "Publishing", "Add a new publisher", "GitHub":

   | Field             | Value                                       |
   |-------------------|---------------------------------------------|
   | Owner             | `sourceperl`                                |
   | Repository name   | `pyModbusTCP`                               |
   | Workflow name     | `release.yml`                               |
   | Environment name  | `pypi` on PyPI, `testpypi` on TestPyPI      |

   A project that doesn't exist yet on an index is added as a "pending publisher" (in "Your account", "Publishing").
4. Once a release has been uploaded by the workflow, delete the API tokens of the projects on PyPI and TestPyPI
   ("Account settings", "API tokens"), and the `~/.pypirc` or keyring entries that may hold one: the workflow is the
   only way to upload.

### release

1. Set `VERSION` (see "version numbers") and the first entry of `CHANGES` (the release date), check the docs pins,
   then build the packages locally with `tools/build_dist.sh -t`: the workflow runs the same checks, a failure there
   is found before the tag.
2. Commit, push to master, wait for the workflow Tests.
3. Tag the commit with the version and push the tag:

   ```bash
   VERSION=$(sed -n "s/^VERSION = '\(.*\)'/\1/p" pyModbusTCP/constants.py)
   git tag -a "v$VERSION" -m "pyModbusTCP $VERSION"
   git push origin "v$VERSION"
   ```

4. The workflow Release (Actions tab) checks that the tag is `v` + `VERSION` (never a `.devN` version), runs the tests,
   builds and checks the 4 files with `tools/build_dist.sh -t`, uploads them to TestPyPI (the library first,
   pyModbusTCP-cli requires it) and installs them from it: the two packages from TestPyPI only (`--no-deps`: never a
   dependency from TestPyPI, where anyone can upload any name), pyserial from PyPI, then the 5 commands.
5. Check the release on https://test.pypi.org/project/pyModbusTCP/ and https://test.pypi.org/project/pyModbusTCP-cli/,
   then approve the deployment `pypi` in the run of the workflow ("Review deployments"): the same files are uploaded
   to PyPI (never built again between the two uploads).
6. Check the result at https://pypi.org/project/pyModbusTCP/ and https://pypi.org/project/pyModbusTCP-cli/.

A failed job can be run again from the run of the workflow ("Re-run failed jobs"): the files of the build are kept
with the run. A version already uploaded to an index can't be uploaded again: on a mistake after the upload to
TestPyPI, set the next version and tag it.

### check a release

The "Provenance" of each file on PyPI (in "Download files", "File details") gives the workflow, the repository and the
commit that built it. With [pypi-attestations](https://pypi.org/project/pypi-attestations/), in a venv:

```bash
pip install pypi-attestations
pypi-attestations verify pypi --repository https://github.com/sourceperl/pyModbusTCP \
    "pypi:pymodbustcp-$VERSION-py3-none-any.whl"
```
