"""Time zones work without a system time zone database (as on Windows).

Windows has no IANA database, so zoneinfo relies on the ``tzdata`` package there. Each
check runs in a fresh interpreter with ``PYTHONTZPATH`` empty, which hides the system
database the same way.
"""

import os
import subprocess
import sys

import pytest

CHECKS = {
    "zoneinfo": (
        "from zoneinfo import ZoneInfo, available_timezones\n"
        "ZoneInfo('UTC'); ZoneInfo('Europe/Bratislava')\n"
        "assert 'Europe/Bratislava' in available_timezones()\n"
    ),
    # The import pipeline's ts.min() on a UTC column failed on Windows like this.
    "polars": (
        "from datetime import datetime, timezone\n"
        "import polars as pl\n"
        "s = pl.Series([datetime(2020, 1, 1, tzinfo=timezone.utc)])\n"
        "s.min().isoformat()\n"
        "s.dt.convert_time_zone('Europe/Bratislava').min().isoformat()\n"
    ),
}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_works_without_system_tz_database(name: str) -> None:
    env = {**os.environ, "PYTHONTZPATH": ""}
    result = subprocess.run(
        [sys.executable, "-c", CHECKS[name]], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
