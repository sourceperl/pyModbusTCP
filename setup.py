from pyModbusTCP import const
from distutils.core import setup

with open('README.rst') as f:
    readme = f.read()

setup(
    name="pyModbusTCP",
    version=const.VERSION,
    description="A simple Modbus/TCP library for Python",
    long_description=readme,
    author="Loic Lefebvre",
    author_email="loic.celine@free.fr",
    license = "MIT",
    url="https://github.com/sourceperl/pyModbusTCP",
    packages=["pyModbusTCP"],
    )
