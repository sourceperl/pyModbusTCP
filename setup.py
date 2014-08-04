from pyModbusTCP import const
from distutils.core import setup

setup(
    name="pyModbusTCP",
    version=const.VERSION,
    description="A simple Modbus/TCP library for Python",
    author="Loic Lefebvre",
    author_email="loic.celine@free.fr",
    license = "MIT",
    url="https://github.com/sourceperl/pyModbusTCP",
    packages=["pyModbusTCP"],
    package_dir={"pyModbusTCP": "pyModbusTCP"},
    )
