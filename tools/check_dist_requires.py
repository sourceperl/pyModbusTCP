#!/usr/bin/env python3
"""Check that the wheels of the two packages require each other in exactly the version of the repository.

pyModbusTCP-cli X must require pyModbusTCP[serial]==X, and the cli extra of pyModbusTCP X must require
pyModbusTCP-cli==X (set by hatch_metadata.py and pyModbusTCP-cli/hatch_metadata_cli.py). Used by tools/build_dist.sh and
the CI.

usage: python tools/check_dist_requires.py DIST_DIR
exit status: 0 OK, 1 a requirement is missing or wrong, 2 usage error or wheel not found
"""

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# (not read from __doc__: None with python -OO)
USAGE = 'usage: python tools/check_dist_requires.py DIST_DIR'


def repo_version() -> str:
    match = re.search(r"^VERSION = '([^']+)'", (ROOT / 'pyModbusTCP' / 'constants.py').read_text(), re.MULTILINE)
    if match is None:
        sys.exit('error: VERSION not found in pyModbusTCP/constants.py')
    return match[1]


def requires_dist(wheel: Path) -> list[str]:
    """The Requires-Dist lines of a wheel (lower case, without the spaces)."""
    with zipfile.ZipFile(wheel) as whl:
        metadata = next(name for name in whl.namelist() if name.endswith('.dist-info/METADATA'))
        lines = whl.read(metadata).decode().splitlines()
    return [line.split(':', 1)[1].replace(' ', '').lower() for line in lines if line.startswith('Requires-Dist:')]


def main() -> int:
    if len(sys.argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    dist = Path(sys.argv[1])
    version = repo_version().lower()
    expected = {
        f'pymodbustcp-{version}-py3-none-any.whl': f"pymodbustcp-cli=={version};extra=='cli'",
        f'pymodbustcp_cli-{version}-py3-none-any.whl': f'pymodbustcp[serial]=={version}',
    }
    status = 0
    for name, requirement in expected.items():
        wheel = dist / name
        if not wheel.is_file():
            print(f'error: {wheel} not found', file=sys.stderr)
            return 2
        if requirement in requires_dist(wheel):
            print(f'{name}: requires {requirement}')
        else:
            print(f'error: {name} does not require {requirement} (Requires-Dist: {requires_dist(wheel)})',
                  file=sys.stderr)
            status = 1
    return status


if __name__ == '__main__':
    sys.exit(main())
