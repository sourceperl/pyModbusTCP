"""Build of pyModbusTCP-cli: require exactly the release of pyModbusTCP of the same version.

The version itself is read from ../pyModbusTCP/constants.py by hatchling ([tool.hatch.version] of pyproject.toml), so
the two packages of the repository can't get out of sync. A build from the sdist of pyModbusTCP-cli (where the
library sources are not available) takes the version and the dependencies from PKG-INFO, without this hook.
"""

from typing import Any

from hatchling.metadata.plugin.interface import MetadataHookInterface


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict[str, Any]) -> None:
        # the code of the commands is in the library: exactly the same release
        metadata['dependencies'] = ['pyModbusTCP[serial]==%s' % metadata['version']]
