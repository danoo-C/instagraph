#!/usr/bin/env python3
"""Start Instagraph and open it in the browser (used by the desktop icon).

    python run.py                 http://127.0.0.1:8000 (this computer only)
    python run.py --lan           also on the local network
    python run.py --port 9000     another port
    python run.py --no-browser    don't open the browser

Run install.py first. Developers can keep using ./launch, which also rebuilds the frontend.
"""

import sys

if sys.version_info < (3, 12):
    sys.stderr.write(
        "Instagraph needs Python 3.12 or newer (this is %d.%d). Run install.py first.\n"
        % sys.version_info[:2]
    )
    sys.exit(1)

from installer.launcher import main

if __name__ == "__main__":
    sys.exit(main())
