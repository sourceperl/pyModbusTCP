"""Sphinx configuration of the pyModbusTCP documentation.

Build it from the root of the repository:

    pip install -r docs/requirements.txt
    sphinx-build -W --keep-going -b html docs docs/_build/html

The library is imported from this checkout (no install needed), so the API reference and the version always match the
sources being documented.
"""

import datetime
import os
import subprocess
import sys

DOCS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(DOCS_DIR)
sys.path.insert(0, ROOT_DIR)

import pyModbusTCP  # noqa: E402 (after the sys.path setup)

# -- Project information -------------------------------------------------------

project = 'pyModbusTCP'
author = 'Loïc Lefebvre'
copyright = '2014-%d, %s' % (datetime.date.today().year, author)
# full version (like 0.4.0.dev0) and short X.Y version
release = pyModbusTCP.__version__
version = '.'.join(release.split('.')[:2])

# -- General configuration -----------------------------------------------------

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'myst_parser',  # Markdown files of the repository included in the docs (SECURITY.md...)
]
source_suffix = {'.rst': 'restructuredtext'}
root_doc = 'index'
language = 'en'
exclude_patterns = ['_build', '_generated', 'Thumbs.db', '.DS_Store']
# guide/tools.rst includes a section of examples/README.md that starts at a level 3 heading (on purpose)
suppress_warnings = ['myst.header']

# autodoc: members in the order of the source code (grouped by role, not sorted by name)
autodoc_member_order = 'bysource'
# the docstring of a class and the one of its __init__() (where the arguments are documented)
autoclass_content = 'both'

# -- HTML output ---------------------------------------------------------------

html_theme = 'sphinx_rtd_theme'
html_theme_options = {'navigation_depth': 3}
html_title = '%s %s documentation' % (project, release)
html_show_sourcelink = False

# -- Other outputs (Read the Docs also builds PDF, EPUB and HTML zip) ----------

latex_documents = [(root_doc, 'pyModbusTCP.tex', 'pyModbusTCP Documentation', author, 'manual')]
man_pages = [(root_doc, 'pymodbustcp', 'pyModbusTCP Documentation', [author], 1)]
texinfo_documents = [(root_doc, 'pyModbusTCP', 'pyModbusTCP Documentation', author, 'pyModbusTCP',
                      'A simple Modbus/TCP library for Python.', 'Miscellaneous')]
epub_show_urls = 'no'


# -- Help of the command line tools --------------------------------------------
# The "--help" of each tool is written to docs/_generated/ at each build and included in tools.rst, so the documented
# options are always those of the code. The gateways need pyserial (in docs/requirements.txt).

TOOLS = ('server', 'client', 'tcp2rtu', 'rtu2tcp')


def write_tools_help() -> None:
    out_dir = os.path.join(DOCS_DIR, '_generated')
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=ROOT_DIR, COLUMNS='100')
    for tool in TOOLS:
        result = subprocess.run([sys.executable, '-m', 'pyModbusTCP.tools', tool, '--help'], env=env, cwd=ROOT_DIR,
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError('"python -m pyModbusTCP.tools %s --help" failed:\n%s' % (tool, result.stderr))
        with open(os.path.join(out_dir, 'help_%s.txt' % tool), 'w', encoding='utf-8') as f:
            f.write(result.stdout)


write_tools_help()
