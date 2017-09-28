from pyModbusTCP import constants
from setuptools import setup

with open('README.rst') as f:
    readme = f.read()

setup(
    name="pyModbusTCP",
    version=constants.VERSION,
    description="A simple Modbus/TCP library for Python",
    long_description=readme,
    author="Loic Lefebvre",
    author_email="loic.celine@free.fr",
    license="MIT",
    url="https://github.com/sourceperl/pyModbusTCP",
    packages=["pyModbusTCP"],
    platforms="any",
)
