"""Build of pyModbusTCP: the cli extra requires exactly the release of pyModbusTCP-cli of the same version.

pyModbusTCP-cli has no code, only the commands and its dependencies: "pip install pyModbusTCP[cli]==X" installs
exactly the commands released (and tested) with X, never another release with other dependencies. The other way is
set by pyModbusTCP-cli/hatch_build.py (pyModbusTCP-cli X requires pyModbusTCP[serial]==X).

The version is read from pyModbusTCP/constants.py by hatchling ([tool.hatch.version] of pyproject.toml): no version to
update by hand. This file is in the sdist, so a build from it (like the one of a downstream packager) runs it too.

The metadata hook of [tool.hatch.metadata.hooks.custom] (path = "hatch_metadata.py" in pyproject.toml): not the default
name hatch_build.py, a second module of that name next to pyModbusTCP-cli/hatch_build.py would stop mypy.
"""

from typing import Any

from hatchling.metadata.plugin.interface import MetadataHookInterface


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict[str, Any]) -> None:
        metadata['optional-dependencies'] = {
            # serial line of the gateways of pyModbusTCP.tools (python -m pyModbusTCP.tools tcp2rtu/rtu2tcp)
            'serial': ['pyserial>=3.5,<4'],
            # commands mbtcp-* in the PATH: a separate package without code (console scripts can't depend on an extra,
            # so a plain install of the library puts no command in the PATH), exactly this release
            'cli': [f"pyModbusTCP-cli=={metadata['version']}"],
        }
