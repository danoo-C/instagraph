"""Local fixtures for importer tests; registers ``fixtures.py`` as ``importer_fixtures``."""

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "importer_fixtures", Path(__file__).with_name("fixtures.py")
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
sys.modules["importer_fixtures"] = _module
_spec.loader.exec_module(_module)
