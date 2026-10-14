"""Build of pyModbusTCP-tools: the version is the one of pyModbusTCP.

The version (and the requirement on exactly this release of pyModbusTCP) is read from ../pyModbusTCP/constants.py,
so the two packages of the repository can't get out of sync. A build from the sdist of pyModbusTCP-tools (where the
library sources are not available) reads it from PKG-INFO, written in the sdist when it was built from the repository.
"""

import re
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).resolve().parent


def version_from_library() -> str:
    """Return the version of pyModbusTCP: VERSION in ../pyModbusTCP/constants.py, or Version in PKG-INFO (sdist)."""
    constants = HERE.parent / 'pyModbusTCP' / 'constants.py'
    if constants.is_file():
        match = re.search(r'''^VERSION\s*=\s*['"]([^'"]+)['"]''', constants.read_text(encoding='utf-8'), re.MULTILINE)
        if not match:
            raise RuntimeError('VERSION not found in %s' % constants)
        return match.group(1)
    pkg_info = HERE / 'PKG-INFO'
    if pkg_info.is_file():
        match = re.search(r'^Version:\s*(\S+)', pkg_info.read_text(encoding='utf-8'), re.MULTILINE)
        if match:
            return match.group(1)
    raise RuntimeError('version of pyModbusTCP not found: build pyModbusTCP-tools from the pyModbusTCP repository')


version = version_from_library()

setup(
    version=version,
    # the code of the commands is in the library: exactly the same release
    install_requires=['pyModbusTCP[serial]==%s' % version],
)
