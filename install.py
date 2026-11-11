#!/usr/bin/env python3
"""Install Instagraph on Windows, macOS or Linux.

    python install.py            (Windows: py install.py)
    python install.py --help     all options

Creates backend/.venv, installs the Python packages, builds the frontend (downloading a
private Node.js if needed) and can put an Instagraph icon on the desktop. Run it again to
update. See docs/plans/cross-platform-installer.md.
"""

import sys

# Keep this check first and free of newer syntax, so old Pythons get a clear message.
if sys.version_info < (3, 12):
    sys.stderr.write(
        "Instagraph needs Python 3.12 or newer, but this is Python %d.%d.\n"
        "Get it from https://www.python.org/downloads/ (Windows: winget install "
        "Python.Python.3.12, macOS: brew install python@3.12, Ubuntu/Debian: "
        "sudo apt install python3.12 python3.12-venv), then run this again with it.\n"
        % sys.version_info[:2]
    )
    sys.exit(1)

from installer.cli import main

if __name__ == "__main__":
    sys.exit(main())
